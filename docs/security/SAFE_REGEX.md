# Package Validation Regular Expressions

Package input validation patterns are administrator-controlled but are still treated
as untrusted executable complexity. Python's standard regular-expression engine uses
backtracking, so a syntactically valid expression can otherwise consume excessive CPU.

Journeyman therefore applies a conservative safe-regex policy before a Package pattern
is saved and again before it is executed. Patterns are limited to 512 characters;
backreferences, lookaround/conditional/advanced group constructs, and unbounded
repetition of whole groups are rejected. Match input is capped at 4096 characters.

Simple anchored validation patterns such as `^[A-Za-z0-9._-]+$` remain supported.
Patterns that need repeated groups should use explicit bounded forms or be rewritten as
a simpler character-class validation. Existing stored patterns that violate the policy
fail closed at launch rather than being executed.
