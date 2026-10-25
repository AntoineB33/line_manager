There is an infinite line from left to right.
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
- the right side is an expression with "and", "or" and intervals of elements with percentages. The position of those intervals are relative to either the position p where the left side is met, or the l left edge of the largest interval that meets the left side and includes p. If the interval is only one position, then it can be p or l, otherwise, it can only be l.

Examples:
- A → B 100%
- A → no B
- A & B → C 45% 3 units on the left of p

A set of element rules can't have a cycle.

Example:
This set of element rules is impossible:
- if A → B 1% 1 unit on the right of l
- if B → A 10%

A "mode rule" is the same as an element rule, but can add or remove elements only at the position of the chip.

Example:
- mode 1 → D 100% & E 100%

The state of the line can be modified only by the user or the "generated script".

The "rules generator" takes the current state of the line and the chip's position and produces a "generated script" to solve optimization problems.
This generated script has events that trigger continuous edits of the line or a new run of the rules generator.
A generated rule must never:
- change the left side of the chip and what is at the position of the chip.
- remove elements added by the user.

Example:
if the optimization problem is a simple presence ratio target for each kind of element, the generated script looks like this:
if the chip is between $x_1$ and $x_2$ and mode is 3, then set A 100% from chip to $x_3$ and B 100% from $x_3$ to $x_4$...
if the chip is between $x_1$ and $x_5$ and mode is 2, then set C 100%...
if D 100% exceeds a presence ratio of y when the chip is between $x_2$ and $x_6$ and mode is 1, then...
if the optimization score becomes less than $z$, then the generator runs again.
if the chip is between $x_7$ and $x_8$ and mode is 2, then the generator runs again.
...

The "allocated resources quantity" hyperparameter in (0, 1) balances between resource usage economy (0) and optimization problem solving (1). When the hyperparameter is lower, then a rules generator run will be faster and use less CPU resources to solve the optimization problems, and the generated script will trigger less frequently a new rules generator run.

The "probability finder" program is used to find probability rules in the mode transitions pattern. This helps the rules generator being more performant with a low allocated resources quantity hyperparameter.

The result of every edit from the user or the generated script must be easily known in advance thanks to intuitive rules.
- If an element rule says "A → B 100% or C 100%", the first operand in the "or" operation that can be placed is used, here element B.
- If an element rule says "A and B → C 100%", A and B are added at x and then a "no C" element is added at x, then the script chooses the combination that loses as few elements as possible, and favors the leftmost elements when there is a tie. Here, only B and C are removed from x.

