"""The machine: an optimizer that generates the texts.  It decides itself when a
new text is relevant and how much time a run gets.  A run may use every CPU,
up to the limit the user sets.  See SEMANTICS.md."""
from __future__ import annotations

import math
import multiprocessing
import os
import pickle
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from fractions import Fraction
from numbers import Real
from typing import Protocol

from chip import Mode, Number
from track import Refused, Text, Track, TrackManager

Clock = Callable[[], float]
Outcome = tuple[float, Text | None, str | None]  # (clock when generate returned, text, error)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                            ctypes.POINTER(ctypes.c_size_t)]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    def _win_check(ok) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())


def _affinity() -> list[int]:
    """The CPUs this process may run on."""
    if sys.platform == "win32":
        mine, system = ctypes.c_size_t(), ctypes.c_size_t()
        _win_check(_k32.GetProcessAffinityMask(_k32.GetCurrentProcess(), ctypes.byref(mine), ctypes.byref(system)))
        return [i for i in range(mine.value.bit_length()) if mine.value >> i & 1]
    if hasattr(os, "sched_getaffinity"):
        return sorted(os.sched_getaffinity(0))
    return list(range(os.cpu_count() or 1))


def usable_cpus() -> int:
    """How many CPUs this process may run on (a run sees its own limit)."""
    return len(_affinity())


@dataclass(frozen=True)
class Resources:
    """The CPU resources of a run: every CPU this process may use, or at most
    `limit` of them."""
    limit: int | None = None

    def __post_init__(self):
        n = usable_cpus()
        if self.limit is not None and (isinstance(self.limit, bool) or not isinstance(self.limit, int)
                                       or not 1 <= self.limit <= n):
            raise ValueError(f"the CPU limit must be an integer from 1 to {n}, got {self.limit!r}")

    @property
    def cpus(self) -> int:
        return usable_cpus() if self.limit is None else self.limit


@dataclass(frozen=True)
class View:
    """A snapshot of the system, so that a run never sees the line change under it."""
    position: Fraction
    mode: Mode
    track: Track
    text_id: str | None
    refused: tuple[Refused, ...]
    time: float
    transitions: tuple[tuple[Fraction, Mode], ...] = ()  # every mode transition, past and planned
    text: Text | None = None  # the installed text, for plan; a run is not sent it


@dataclass(frozen=True)
class Deadline:
    """The time a run allowed itself: from `start`, at most `budget` seconds of `clock`."""
    start: float
    budget: float
    clock: Clock

    @property
    def end(self) -> float:
        return self.start + self.budget

    def remaining(self) -> float:
        return max(0.0, self.end - self.clock())

    def expired(self) -> bool:
        return self.clock() > self.end


class Optimizer(Protocol):
    def plan(self, view: View, resources: Resources) -> float | None:
        """Called whenever no run is in progress.  Return how many seconds to
        allow a new run (positive, finite), or None to start none now."""
        ...

    def generate(self, view: View, deadline: Deadline, resources: Resources) -> Text | None:
        """One run.  Return the new text, or None for no new text.  The run is
        stopped at `deadline.end`; a result that arrives later is discarded."""
        ...


class Job(Protocol):
    stopped: bool
    stopped_at: float | None

    def done(self) -> bool: ...
    def result(self) -> Outcome: ...
    def stop(self) -> None:
        """Hard stop: the run and every process it started end now."""
        ...


class Runner(Protocol):
    def start(self, optimizer: Optimizer, view: View, deadline: Deadline, resources: Resources) -> Job: ...


@dataclass(frozen=True)
class Run:
    id: int
    view: View                     # what the run was given
    deadline: Deadline
    finished: float | None = None  # clock when generate returned, or when the run was stopped
    text_id: str | None = None     # id of the text it returned
    installed_at: Fraction | None = None  # chip position when its text was installed
    outcome: str = "running"       # running | installed | late | stopped | no text | error | refused
    reason: str = ""


class Machine:
    """Drives a TrackManager with an optimizer.

    After every observation (start, chip move, user edit, `poll`), the run in
    progress is handled first: past its deadline it is stopped; finished, its
    text is installed at the chip's current position if it kept to its deadline,
    and discarded otherwise.  Then, if no run is in progress, the optimizer's
    `plan` decides whether to start one and how long it may take.  At most one
    run is in progress at a time.  Nothing the optimizer does raises here:
    every failure is recorded in `runs` or `refused_plans`."""

    def __init__(self, manager: TrackManager, optimizer: Optimizer, resources: Resources | None = None,
                 clock: Clock = time.monotonic, runner: Runner | None = None):
        self.manager = manager
        self.optimizer = optimizer
        self.resources = resources or Resources()  # the user may change it at any time
        self.clock = clock  # the default runner needs time.monotonic: it is the same in every process
        self.runner = runner or ProcessRunner()
        self.runs: list[Run] = []
        self.refused_plans: list[tuple[View, object, str]] = []  # (view, what plan returned, why)
        self._job: Job | None = None
        self.poll()

    @property
    def running(self) -> bool:
        return self._job is not None

    def update_chip(self, new_position: Number) -> None:
        self.manager.update_chip(new_position)
        self.poll()

    def user_edit(self, ops) -> int:
        i = self.manager.user_edit(ops)
        self.poll()
        return i

    def poll(self) -> None:
        """Handle the run in progress, then let the optimizer decide on a new one."""
        if self._job is not None:
            job, run = self._job, self.runs[-1]
            if not job.done() and self.clock() > run.deadline.end:
                job.stop()
            if not job.done():
                return
            self._job = None
            self.runs[-1] = self._finish(run, job)
        self._maybe_start()

    def close(self) -> None:
        """Stop the run in progress, if any, and start no other."""
        if self._job is not None:
            job, self._job = self._job, None
            job.stop()
            self.runs[-1] = self._finish(self.runs[-1], job)

    # ---- internals --------------------------------------------------------
    def _view(self) -> View:
        m = self.manager
        return View(m.chip.position, m.chip.mode, dict(m.track()), m.current_text_id,
                    tuple(m.refused), self.clock(), tuple(m.chip.transitions), m.text)

    def _maybe_start(self) -> None:
        view, resources = self._view(), self.resources
        try:
            budget = self.optimizer.plan(view, resources)
        except Exception as err:  # noqa: BLE001 - plan is the optimizer's code: any failure is recorded
            self.refused_plans.append((view, None, f"plan raised {err!r}"))
            return
        if budget is None:
            return
        if isinstance(budget, bool) or not isinstance(budget, Real) or not math.isfinite(budget) \
                or not budget > 0:
            self.refused_plans.append((view, budget, "a run needs a positive, finite number of seconds"))
            return
        deadline = Deadline(self.clock(), budget, self.clock)
        view = replace(view, text=None)  # a text installed directly need not be picklable
        run = Run(len(self.runs), view, deadline)
        try:
            job = self.runner.start(self.optimizer, view, deadline, resources)
        except Exception as err:  # noqa: BLE001 - for example an optimizer that cannot be sent to a process
            self.runs.append(replace(run, finished=self.clock(), outcome="error", reason=repr(err)))
            return
        self.runs.append(run)
        self._job = job

    def _finish(self, run: Run, job: Job) -> Run:
        if job.stopped:
            return replace(run, finished=job.stopped_at, outcome="stopped")
        finished, text, err = job.result()
        run = replace(run, finished=finished, text_id=text.id if isinstance(text, Text) else None)
        if err is not None:
            return replace(run, outcome="error", reason=err)
        if finished > run.deadline.end:
            return replace(run, outcome="late", reason=f"returned after {finished - run.deadline.start}s, "
                                                       f"budget {run.deadline.budget}s")
        if text is None:
            return replace(run, outcome="no text")
        position = self.manager.chip.position
        try:
            self.manager.switch_rules_text(text)
        except AssertionError:
            raise
        except Exception as err:  # noqa: BLE001 - a text's rules are the optimizer's code; the switch is a transaction: nothing changed
            return replace(run, outcome="refused", reason=repr(err))
        return replace(run, outcome="installed", installed_at=position)


# ---- runs in their own processes --------------------------------------------
class _Group:
    """The processes of one run, kept on `cpus` and ended together.

    Windows: a job object holds them; the operating system keeps every process
    of the job on `cpus`, and ends all of them on `kill` or when the job is
    closed.  Elsewhere: the run is a new process group whose CPU affinity is
    set before it starts working; processes it starts inherit both."""

    def __init__(self, cpus: list[int]):
        self.cpus = cpus
        self.pid: int | None = None
        self._job = None
        if sys.platform == "win32":
            job = _k32.CreateJobObjectW(None, None)
            _win_check(job)
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x10  # KILL_ON_JOB_CLOSE | AFFINITY
            limits.BasicLimitInformation.Affinity = sum(1 << c for c in cpus)
            ok = _k32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            if not ok:
                _k32.CloseHandle(job)
            _win_check(ok)
            self._job = job

    def add(self, pid: int) -> None:
        self.pid = pid
        if sys.platform == "win32":
            process = _k32.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
            _win_check(process)
            try:
                _win_check(_k32.AssignProcessToJobObject(self._job, process))
            finally:
                _k32.CloseHandle(process)
        elif hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(pid, self.cpus)
        elif len(self.cpus) < (os.cpu_count() or 1):
            raise NotImplementedError("this platform cannot limit the CPUs of a process")

    def kill(self) -> None:
        if sys.platform == "win32":
            if self._job is not None:
                _k32.TerminateJobObject(self._job, 1)
        elif self.pid is not None:
            for kill, target in ((os.killpg, self.pid), (os.kill, self.pid)):
                try:
                    kill(target, signal.SIGKILL)
                except OSError:
                    continue
                break

    def close(self) -> None:
        self.kill()
        if sys.platform == "win32" and self._job is not None:
            _k32.CloseHandle(self._job)
            self._job = None


def _child(conn, optimizer: Optimizer, view: View, deadline: Deadline, resources: Resources) -> None:
    """The run's process."""
    conn.recv()  # wait until the parent has put this process under its limits
    if sys.platform != "win32":
        os.setsid()  # a process group of its own, so that a hard stop reaches every process it starts
    try:
        out: Outcome = (deadline.clock(), optimizer.generate(view, deadline, resources), None)
    except Exception as err:  # noqa: BLE001 - generate is the optimizer's code: any failure is recorded
        out = (deadline.clock(), None, repr(err))
    try:
        conn.send(out)
    except Exception as err:  # noqa: BLE001 - the text cannot be pickled, whatever the reason
        conn.send((out[0], None, f"the result cannot be sent back: {err!r}"))


class ProcessJob:
    def __init__(self, optimizer: Optimizer, view: View, deadline: Deadline, resources: Resources):
        # Fails here, before any process exists, if something cannot be sent.
        pickle.dumps((optimizer, view, deadline, resources))
        ctx = multiprocessing.get_context("spawn")
        self._conn, child = ctx.Pipe()
        self._lock = threading.Lock()
        self._result: Outcome | None = None
        self.stopped = False
        self.stopped_at: float | None = None
        self._clock = deadline.clock
        self._group = _Group(_affinity()[:resources.cpus])
        # Not a daemon: a run may start processes of its own, to use its CPUs.
        self._process = ctx.Process(target=_child, args=(child, optimizer, view, deadline, resources))
        try:
            self._process.start()
            child.close()
            assert self._process.pid is not None
            self._group.add(self._process.pid)
            self._conn.send("go")
        except BaseException:
            if self._process.pid is not None:
                self._process.kill()
            self._group.close()
            raise
        self._timer = threading.Timer(deadline.remaining(), self.stop)
        self._timer.daemon = True
        self._timer.start()

    def done(self) -> bool:
        with self._lock:
            return self.stopped or self._result is not None or self._take() or not self._process.is_alive()

    def result(self) -> Outcome:
        with self._lock:
            if self._result is None and not self._take():
                code = self._process.exitcode
                self._result = (self._clock(), None, f"the run's process ended with code {code} and no result")
                self._end()
            assert self._result is not None
            return self._result

    def stop(self) -> None:
        with self._lock:
            if self.stopped or self._result is not None or self._take():
                return
            self.stopped, self.stopped_at = True, self._clock()
            self._end()

    def _take(self) -> bool:
        """Read the result if it has arrived; then end what the run left behind."""
        if self._result is not None:
            return True
        try:
            if not self._conn.poll():
                return False
            self._result = self._conn.recv()
        except (EOFError, OSError) as err:
            self._result = (self._clock(), None, f"the result was lost: {err!r}")
        except Exception as err:  # noqa: BLE001 - for example a text whose functions cannot be found here
            self._result = (self._clock(), None, f"the result cannot be read: {err!r}")
        self._end()
        return True

    def _end(self) -> None:
        self._timer_cancel()
        self._group.close()
        self._process.join(5)
        self._conn.close()

    def _timer_cancel(self) -> None:
        timer = getattr(self, "_timer", None)
        if timer is not None and timer is not threading.current_thread():
            timer.cancel()


class ProcessRunner:
    """Each run in a new process, limited to `resources.cpus` CPUs together with
    every process it starts, and stopped hard at its deadline even if nothing
    observes the machine then.  The optimizer, the view and the returned text
    are sent between processes, so they must be picklable."""

    def start(self, optimizer: Optimizer, view: View, deadline: Deadline, resources: Resources) -> ProcessJob:
        return ProcessJob(optimizer, view, deadline, resources)
