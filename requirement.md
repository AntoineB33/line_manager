There is an infinite line from −∞ on the left to ∞ on the right.
There is a 0-dimensional chip on the line that moves to the right, with a mathematically continuous movement.
There are different kinds of elements. A state of the line is a set of intervals each associated to an element type and a percentage. The same element type can't be present several times at the same position on the line.

Example of a state of the line:
- A at [$x_1$, $x_2$) 100%
- A at $x_2$ 50%
- B at $x_3$ 100%
- C at $x_1$ 100%

There are several modes, and only one is applied for one position of the chip on the line. Every bounded stretch of the line contains finitely many mode transitions. The mode transitions are given externally.

There are "element rules". In an element rules, there are two sides:
- the left side is a boolean expression manipulating element types and percentages. An element with 0% is considered absent.
- the right side is an expression with "and", "or" and intervals of elements with percentages. The position of those intervals are relative to either the position p where the left side is met, or the l left edge (bound or unbound) of the largest interval that meets the left side and includes p.

Examples:
- A → B 100%
- A → B 0%
- A & B → C 45% 3 units on the left of p

If there is a "or" in the right side of the element rule, the leftmost operand (leftmost in the expression) that can be chosen is chosen.

A "mode rule" is the same as an element rule, but can add or remove elements only at the position of the chip.

Example:
- mode 1 → D 100% & E 100%

The state of the line can be modified only by the user (changing the mode, adding or removing elements) and the "generated script".

The "rules generator" takes the current state of the line and the chip's position and produces a "generated script" to solve optimization problems and satisfy constraints.
This generated script is a very concise text showing events and the corresponding triggers for continuous edits of the line or a new run of the rules generator.
A generated rule must never:
- change the left side of the chip and what is at the position of the chip.
- remove elements added by the user.
At the absolute start of the program, the generated script is only an instruction to run the rules generator once as soon as possible. The generated script is changed only when the generator finds a new one or the user edits it. If the generator is called to run again while it is already running, the previous run is cancelled and the new one starts.

Example:
if the optimization problem is a simple presence ratio target for each kind of element, the generated script looks like this:
if the chip is between $x_1$ and $x_2$ and mode is 3, then set A 100% from chip to $x_3$ and B 100% from $x_3$ to $x_4$...
if the chip is between $x_1$ and $x_5$ and mode is 2, then set C 100%...
if D 100% exceeds a presence ratio of y when the chip is between $x_2$ and $x_6$ and mode is 1, then...
if the optimization score becomes less than $z$, then the generator runs again.
if the chip is between $x_7$ and $x_8$ and mode is 2, then the generator runs again.
...

The "allocated resources quantity" hyperparameter in (0, 1) balances between resource usage economy (0) and optimization problem solving (1). When the hyperparameter is lower, then a rules generator run will be faster and use less CPU resources to solve the optimization problems, and the current generated script will trigger less frequently a new rules generator run.

A run of the rules generator is progressive: it publishes a series of generated scripts, each replacing the previous one, and ends after publishing the last one. The generated script is "definitive" up to a position x when every next script of the same run gives the same instructions for the chip's positions before x, whatever the modes. If a run has made the script definitive up to x, then 10 seconds later it must have made it definitive up to x + 10 units, using an approximation if the exact result can't be found in time. An approximation may lower the optimization score but never breaks a constraint. At the start of a run, the generator must instantly publish a new script that is definitive on the 10 units on the right of the chip. The duration of a run of the rules generator depends on the allocated resources quantity hyperparameter.
To satisfy this pace, the generator can look at the current generated script.

The "probability finder" program is used to find probability rules in the mode transitions pattern. This helps the rules generator being more performant with a low allocated resources quantity hyperparameter.

Edits from the user win over what is on the line. Nothing more than necessary is changed from the older elements, or more if it helps keeping a fast computation. If a result cannot be computed in reasonable time, the edit is refused. If the edit leads to a contradiction with the constraints the generated script must satisfy, the edit is refused. For example:
"A → B 100% or C 100%"
A and B at [1, 2]
user edit: remove B at 1.5
Now A at [1, 2], B at [1, 1.5)U(1.5, 2] and C at 1.5.


The line is computed from all the user's edits, kept in the order they were made. Each edit adds or removes elements on intervals. Where edits contradict each other, directly or through the rules, the newer one wins. An older edit gives way only where, and as long as, it contradicts newer ones: when that contradiction disappears, it applies again.

Every calculation, except for the run of the rules generator, must be considered instantaneous. If the user does an edit that takes time to compute, then when done the state of the line is shown as if the edit was instantaneous. During the calculation, the display of the line doesn't stop and continues to update smoothly, then when it is complete it instantly updates as if the calculation happened instantly. For example, the user changes a mode that must cut the element the chip was pushing forward, but it takes time. So the chip continues to push the element forward, and when the calculation is done, the element has a hole behind the chip as if it started to cut the element at the chip's position when the mode was changed.
For the rules generator however, when a new generated script is published, it is applied and doesn't revert the display. When the rules generator starts, it takes a snapshot of the current state of the line and the chip's position, and uses it to compute the generated script. That means that if it returns a new script, it could be that the current state already disrupts the new plans so much, that the new script instantly triggers a new run.