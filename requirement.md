There is an infinite line from left to right.
There is a 0-dimensional chip on the line that moves to the right, with a mathematically continuous movement.
There are different kinds of elements. A state of the line is a set of intervals each associated to an element type and a percentage. The same element type can't be present several times at the same position on the line.

Example of a state of the line:
- A at [$x_1$, $x_2$) 100%
- A at $x_1$ 50%
- B at $x_3$ 100%
- C at $x_1$ 100%

There are several modes, and only one is applied for one position of the chip on the line. Every bounded stretch of the line contains finitely many mode transitions. The mode transitions are given externally.

There are "element rules". In an element rules, there are two sides:
- the left side is a combination of element types and/or a mode
- the right side is a set of intervals of elements with percentages.
Where the left side is met, the right side must be applied.

Examples:
- A → B
- A → no B
- A & B → C 3 units on the left
- mode 1 → D & E

The state of the line can be modified only by the user or the "generated script".

The "rules generator" takes the current state of the line and the chip's position and produces a "generated script" to solve optimization problems.
This generated script has events that trigger continuous edits of the line or a new run of the rules generator.
A generated rule must never:
- change the left side of the chip and what is at the position of the chip.
- remove elements added by the user.

Example 1:
A → A at -1
chip at 0
The text of rules is empty because it can do nothing without violating the rules. The user however can add A anywhere.

Example:
if the optimization problem is a simple presence ratio target for each kind of element, the generated script looks like this:
if the chip is between $x_1$ and $x_2$ and mode is 3, then set element A from chip to $x_3$ and element B from $x_3$ to $x_4$...
if the chip is between $x_1$ and $x_5$ and mode is 2, then set element C...
if element D exceeds a presence ratio of y when the chip is between $x_2$ and $x_6$ and mode is 1, then...
if the optimization score becomes less than $z$, then the generator runs again.
if the chip is between $x_7$ and $x_8$ and mode is 2, then the generator runs again.
...

The "allocated resources quantity" hyperparameter in (0, 1) balances between resource usage economy (0) and optimization problem solving (1). When the hyperparameter is lower, then a rules generator run will be faster and use less CPU resources to solve the optimization problems, and the generated script will trigger less frequently a new rules generator run.

The "probability finder" program is used to find probability rules in the mode transitions pattern. This helps the rules generator being more performant with a low allocated resources quantity hyperparameter.