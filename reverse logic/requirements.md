The user can do edits. One edit is a finite set of additions, removals, extensions, retractions and shifts of finite sets of intervals or points of elements.
An extension adds positions that touch an element of the same kind, so that the element grows. A retraction removes positions from an element of the same kind while leaving the rest of the element touching them, so that the element shrinks.
Removing, moving or retracting something that is not on the line is refused.
An edit that puts a kind where that kind is already present (by adding, moving or extending) is refused.
If the user adds an element, then removes it, the state of the line is the same as before the addition.
If the user removes an element, then adds it, the state of the line is the same as before the removal.
If the user moves some elements, then moves them back to their original place, the state of the line is the same as before the move.
If the user extends an element, then retracts it back, the state of the line is the same as before the extension.
If the user retracts an element, then extends it back, the state of the line is the same as before the retraction.
Let's suppose edit A and edit B, where edit B doesn't require edit A (e.g., edit A moves element A onto x, edit B moves element B onto x). If the user do edits A, then do edits B, the user can reverse edits A, and the state of the line is the same as if edits A never happened. If the user then reverses edit B, the state of the line is the same as before edit A.
When the user does an edit (adding, removing, moving, extending or retracting something) and elements already in the line prevent it from their rules, then they must adapt to make it possible: the newest edit wins. For example, with the rule "if element A, then element B", removing element B makes element A disappear there. If the edit is contradictory by itself, the edit is not allowed.
If the user drags a block of element somewhere before coming back to the initial place, the line ends up in its initial state. During a drag, the line is updated at every step of the drag, and after each step it is exactly what it would be if the block had been moved directly to its current position: it doesn't depend on the previous steps, and no update is delayed or skipped (no debounce).

Example 1:
Suppose the positions $x_1$ < $x_2$ < $x_3$ < $x_4$ < $x_5$ < $x_6$ < $x_7$ < $x_8$ < $x_9$ < $x_10$.
Suppose the rules:
- if element D, then no element C
Suppose the line:
element A at [$x_1$; $x_2$]
element B at [$x_1$; $x_4$]
element D at [$x_6$; $x_7$]
The user defines edit A:
- element A at [$x_1$; $x_2$[ will be moved at $x_3$ (the start of the set being moved is at $x_3$ now)
- element B at $x_4$ will be moved at $x_5$ 
- element C will be added at [$x_6$; $x_7$]
The user does edit A.
Now [$x_1$; $x_4$[ has element B but not $x_4$.
$x_2$ has element A, but not [$x_1$; $x_2$[.
The user defines edit B:
- element D will be added at [$x_6$; $x_7$]
The user does edit B.
Now there is element D at [$x_6$; $x_7$].
The user reverses edit B.
Now there is element C at [$x_6$; $x_7$].
The user reverses edit A.
Now the line is back to its initial state.
The user defines edit C:
- element C will be added at $x_8$
- element D will be added at $x_8$
Edit C can't be done because it contradicts the rules.

Example 2:
Suppose the positions $x_1$ < $x_2$ < $x_3$ < $x_4$ < $x_5$, with $x_5$ - $x_4$ = $x_2$ - $x_1$.
Suppose the rules:
- if element A, then no element B
Suppose the line:
element A at [$x_1$; $x_3$]
The user adds element B at [$x_1$; $x_2$] and drags it with the mouse to the right. As the user drags it, the interval of element A is cut by the interval of element B moving to the right. When the user drags it past element A, it is now element A at [$x_1$; $x_3$] and element B at [$x_4$; $x_5$].

Example 3:
Suppose the positions $x_1$ < $x_2$ < $x_3$ < $x_4$ < $x_5$, with $x_5$ - $x_4$ = $x_2$ - $x_1$.
Suppose the rules:
- if element D, then no element C
Suppose the line:
element D at $x_3$
The user adds element C at [$x_1$; $x_2$] and drags it with the mouse to the right, until it is at [$x_4$; $x_5$].
As long as element C doesn't reach $x_3$, there is element D at $x_3$.
At every step where element C covers $x_3$, there is no element D.
At the first step where element C is past $x_3$, element D is back at $x_3$: not at a later step.
At the end, it is element D at $x_3$ and element C at [$x_4$; $x_5$], the same as if element C had been added directly at [$x_4$; $x_5$].

The rules have restrictions:
There can't be cycle between kinds of elements, whatever the distances in the rules. For example, this set of rules is not possible:
- if element A, then element B 1 unit on the right
- if element B, then element A 1 unit on the right

The result of every edit must be unique: there must be no ambiguity. For example, if a rule says "if element A then element B or C", the first operand that can be placed is used, here element B.