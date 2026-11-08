## How this book works

The book has two halves that are meant to be used together.

**The textbook** is a sequence of chapters. Every chapter starts with *why*: a concrete problem, often from a
trading system, that the concept solves. Then it builds the idea up step by step. Every section ends with a few
exercises, with solutions hidden in dropdowns, so you can check your understanding before moving on.

**The practice set** is a collection of graded problems tagged by topic, each with tests. Write your solution in the
browser, press **Run tests**, and it is compiled with `g++ -std=c++20 -O2` under AddressSanitizer and
UndefinedBehaviorSanitizer on your own machine.

## Who it's for

You should be comfortable programming in *some* language. You don't need to know C++ already: Part I starts from
how a program is compiled. If you already write C++, skim Part I and start with
[Part II · Performance Engineering](/lessons/).

## Suggested path

1. Read a chapter and do its section exercises.
2. Do the practice problems linked at the end of the chapter.
3. Every few chapters, revisit a problem you solved earlier and try to make it faster, then *measure* it.

> [!TIP]
> Keep a scratch directory with a `CMakeLists.txt` (or just a shell alias for `g++ -std=c++20 -O2 -Wall -Wextra
> -fsanitize=address,undefined`) so trying an idea from a chapter takes seconds.
