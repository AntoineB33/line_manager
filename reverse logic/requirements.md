The user can do edits. One edit is a finite set of additions, removals, extensions, retractions and shifts of finite sets of intervals or points of elements.
If the user adds an element, then removes it, the state of the line is the same as before the addition.
If the user removes an element, then adds it, the state of the line is the same as before the removal.
If the user moves some elements, then moves them back to their original place, the state of the line is the same as before the move.
Let's suppose edit A and edit B, where edit B doesn't require edit A (e.g., edit A moves element A onto x, edit B moves element B onto x). If the user do edits A, then do edits B, the user can reverse edits A, and the state of the line is the same as if edits A never happened. If the user then reverses edit B, the state of the line is the same as before edit A.
When the user adds something or moves something at position x, and that elements in the line prevent it from their rules, then they must adapt to make it possible. If it is contradictory, the edit is not allowed.
The reversing can be done at one time or in a continuous time. If the user drags a block of element somewhere before coming back to the initial place, the line is updated in a mathematically continuous way before ending up in its initial state.

Example 1:
Suppose the positions $x_1$ < $x_2$ < $x_3$ < $x_4$ < $x_5$ < $x_6$+ < $x_7$ < $x_8$ < $x_9$ < $x_10$.
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
Suppose the positions $x_1$ < $x_2$ < $x_3$ < $x_4$ < $x_5$.
Suppose the rules:
- if element A, then no element B
Suppose the line:
element A at [$x_1$; $x_3$]
The user adds element B at [$x_1$; $x_2$] and drags it with the mouse to the right. As the user drags it, the interval of element A is cut by the interval of element B moving to the right. When the user drags it past element A, it is now element A at [$x_1$; $x_3$] and element B at [$x_4$; $x_5$].

The rules have restrictions:
There can't be cycle. For example, this set of rules is not possible:
- if element A, then element B 1 unit on the right
- if element B, then element A 1 unit on the right

The requirements on reversal imply there should be no ambiguity. For example, if a rule says "if element A then element B or C", and the user adds element A where there is either one of them, then the first operand is used, here element B.