In a line, there can be different kinds of elements in it.
There can also be rules for each kind of element.
Examples:
- when there is element A, there must be element B
- when there is element A, there is not element B
- when there is element A and B, there is element C 3 units before
The user can do edits. On edit is a finite set of additions, removals and shifts of finite sets of intervals or points of elements.
If the user adds an element, then removes it, the state of the line is the same as before the addition.
If the user removes an element, then adds it, the state of the line is the same as before the removal.
If the user moves some elements, then moves them back to their original place, the state of the line is the same as before the move.
Let's suppose edit A and edit B, where edit B doesn't require edit A (e.g., edit A moves element A onto x, edit B moves element B onto x). If the user do edits A, then do edits B, the user can reverse edits A, and the state of the line is the same as if edits A never happened. If the user then reverses edit B, the state of the line is the same as before edit A.
