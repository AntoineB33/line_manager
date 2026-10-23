The user can do edits. An edit can be adding intervals of elements, removing intervals of elements or moving intervals of elements.
An edit that puts a kind where that kind is already present is refused.
Removing or moving something on the line that doesn't exist is refused.
The user can reverse an edit. If the effect of the edit is not present on the line, the reverse is refused. If it is partially present, the reverse is partially done, and the rest is refused.
If the user does edit 1, then edit 2, then reverses edit 2, then reverses edit 1, the state of the line on the modified parts is the same as if edits 1 and 2 never happened.


Suppose A [1, 2] and B [3, 4].
edit 1: remove A
edit 2: remove B
Do the requirements guarantee imply that if the user does edit 1, then edit 2, then reverse edit 1, then reverse edit 2, the state of the line will be the same as if edits 1 and 2 never happened? If we consider the program that is created to satisfy the requirements doesn't have things like if 


Suppose A [1, 2] and B [3, 4].
edit 1: remove A at [1, 2]
edit 2: remove B at [3, 4]
edit 3: nothing
If the user does edit 1, then edit 3, then reverses edit 3, then reverses edit 1, then according to the requirements, the state of the line in [1, 2] is the same as if edit 1 never happened.
In other words, the requirements ensure that if the user does an edit then reverses it, the state of the line on the modified parts is the same as if the edit never happened.
If the user does edit 1, then edit 2, then reverses edit 1, then reverses edit 2, the state of the line on the modified parts is the same as if edits 1 and 2 never happened.

Example 1:
Suppose the positions $x_1$ < $x_2$ < $x_3$ < $x_4$ < $x_5$ < $x_6$ < $x_7$ < $x_8$ < $x_9$ < $x_{10}$.
Suppose the rules:
- if element D, then no element C
Suppose the line:
element A at [$x_1$, $x_2$]
element B at [$x_1$, $x_4$]
element D at [$x_6$, $x_7$]
The user defines edit 1:
- element A at [$x_1$, $x_2$) will be shifted so it begins at $x_3$ (the start of the set being moved is at $x_3$ now)
- element B at $x_4$ will be moved to $x_5$ 
- element C will be added at [$x_6$, $x_7$]
The user does edit 1.
Now [$x_1$, $x_4$) has element B but not $x_4$.
$x_2$ has element A, but not [$x_1$, $x_2$).
The user defines edit 2:
- element D will be added at [$x_6$, $x_7$]
The user does edit 2.
Now there is element D at [$x_6$, $x_7$].
The user reverses edit 2.
Now there is element C at [$x_6$, $x_7$].
The user reverses edit 1.
Now the line is back to its initial state.
The user defines edit 3:
- element C will be added at $x_8$
- element D will be added at $x_8$
Edit 3 can't be done because it contradicts the rules.

Example 2:
Suppose the positions $x_1$ < $x_2$ < $x_3$ < $x_4$ < $x_5$, with $x_5$ - $x_4$ = $x_2$ - $x_1$.
Suppose the rules:
- if element A, then no element B
Suppose the line:
element A at [$x_1$, $x_3$]
The user adds element B at [$x_1$, $x_2$] and drags it with the mouse to the right. As the user drags it, the interval of element A is cut by the interval of element B moving to the right. When the user drags it past element A, it is now element A at [$x_1$, $x_3$] and element B at [$x_4$, $x_5$].

Example 3:
Suppose the positions $x_1$ < $x_2$ < $x_3$ < $x_4$ < $x_5$, with $x_5$ - $x_4$ = $x_2$ - $x_1$.
Suppose the rules:
- if element D, then no element C
Suppose the line:
element D at $x_3$
The user adds element C at [$x_1$, $x_2$] and drags it with the mouse to the right, until it is at [$x_4$, $x_5$].
While the leading edge of element C has not yet reached $x_3$, element D remains at $x_3$.
At every step where element C covers $x_3$, there is no element D.
At the first step where element C is past $x_3$, element D is back at $x_3$: not at a later step.
At the end, it is element D at $x_3$ and element C at [$x_4$, $x_5$], the same as if element C had been added directly at [$x_4$, $x_5$].

Examples:
rules: "A and B and C → D", "A and B → C".
The line is empty.
edit 1: add A at x. It is A {x}.
edit 2: add B at x. It is A {x}, B {x}, C {x}, D {x}.
edit 3: remove A at x. It is B{x}.
edit 4: remove B at x. It is empty.

rules: "A and B and C → D", "X → no D", "A and B → C".
The line is empty.
edit 1: add A and B at x. It is A {x}, B {x}, C {x}, D {x}.
edit 2: add X at x. It is A {x}, X{x}.
edit 3: remove A at x. It is B {x}, X {x}.
edit 4: remove X at x. It is B {x}.
edit 5: add A at x. It is A {x}, B {x}, C {x}, D {x}.