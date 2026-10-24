The user can do edits, which is adding and removing intervals of elements on the line. The reverse of an edit is the same edit but with the opposite action (adding or removing).
If the user does edit 1, then edit 2, then reverse edit 2, then reverse edit 1, then the state of the line is the same as before edit 1.

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

The rules have restrictions:
There can't be a cycle between kinds of elements, whatever the distances in the rules. For example, this set of rules is not possible:
- if element A, then element B 1 unit on the right
- if element B, then element A 1 unit on the right

The result of every edit must be unique: there must be no ambiguity.
- If a rule says "A → B or C", the first operand in the "or" operation that can be placed is used, here element B.
- If a rule says "A and B → C", A and B are added at x and then a "no C" element is added at x, then the script chooses the combination that looses as less element as possible, and favors the leftmost elements when there is a tie. Here, only B and C are removed from x.
- When several positions compete for the operands of an "or" (one position taking an operand prevents another from taking its first one), positions are decided one after the other: the closest to 0 first, and the one on the left first when two are equally close. Each takes the first operand of the "or" that still lets the remaining positions be placed. This also applies when the element extends infinitely, in which case the result may repeat infinitely.

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