+++
title = "Build Your Own Type Traits"
difficulty = "medium"
tags = ["templates", "metaprogramming"]
+++

Type traits are compile-time functions on types, implemented with template specialization. Implement these
without using `<type_traits>`:

```cpp
template <class A, class B> struct is_same;          // ::value == true iff A and B are the same type
template <class T> struct remove_reference;          // ::type strips & and &&
template <class T> struct remove_const;              // ::type strips top-level const
template <class T> struct is_pointer;                // ::value, true for T* (including const pointers T* const)
template <bool B, class T, class F> struct conditional;   // ::type is T if B, else F
```

Plus the `_v` / `_t` helpers: `is_same_v`, `remove_reference_t`, `remove_const_t`, `is_pointer_v`,
`conditional_t`. Put everything in namespace `mytraits`.

## Starter Code

```cpp
#pragma once

namespace mytraits {

template <class A, class B> struct is_same { static constexpr bool value = false; };
template <class A, class B> inline constexpr bool is_same_v = is_same<A, B>::value;

template <class T> struct remove_reference { using type = T; };
template <class T> using remove_reference_t = typename remove_reference<T>::type;

template <class T> struct remove_const { using type = T; };
template <class T> using remove_const_t = typename remove_const<T>::type;

template <class T> struct is_pointer { static constexpr bool value = false; };
template <class T> inline constexpr bool is_pointer_v = is_pointer<T>::value;

template <bool B, class T, class F> struct conditional { using type = T; };
template <bool B, class T, class F> using conditional_t = typename conditional<B, T, F>::type;

}  // namespace mytraits
```

## Hints

- The primary template gives the default answer. A **partial specialization** like
  `template <class T> struct is_same<T, T>` matches only when both arguments are the same type.
- `is_pointer<T* const>` needs its own specialization, or strip the cv-qualifiers first.

## Solution

```cpp
#pragma once

namespace mytraits {

template <class A, class B> struct is_same { static constexpr bool value = false; };
template <class T> struct is_same<T, T> { static constexpr bool value = true; };
template <class A, class B> inline constexpr bool is_same_v = is_same<A, B>::value;

template <class T> struct remove_reference { using type = T; };
template <class T> struct remove_reference<T&> { using type = T; };
template <class T> struct remove_reference<T&&> { using type = T; };
template <class T> using remove_reference_t = typename remove_reference<T>::type;

template <class T> struct remove_const { using type = T; };
template <class T> struct remove_const<const T> { using type = T; };
template <class T> using remove_const_t = typename remove_const<T>::type;

template <class T> struct is_pointer_impl { static constexpr bool value = false; };
template <class T> struct is_pointer_impl<T*> { static constexpr bool value = true; };
template <class T> struct is_pointer : is_pointer_impl<remove_const_t<T>> {};
template <class T> inline constexpr bool is_pointer_v = is_pointer<T>::value;

template <bool B, class T, class F> struct conditional { using type = T; };
template <class T, class F> struct conditional<false, T, F> { using type = F; };
template <bool B, class T, class F> using conditional_t = typename conditional<B, T, F>::type;

}  // namespace mytraits
```

The compiler picks the most specialized template that matches, so "pattern matching on types" is just partial
specialization. All of this is evaluated during compilation and generates no code. The real `<type_traits>`
also handles `volatile`, but works the same way.

## Tests

```cpp
using namespace mytraits;
int main() {
    CHECK((is_same_v<int, int>));
    CHECK((!is_same_v<int, long>));
    CHECK((!is_same_v<int, const int>));
    CHECK((is_same_v<remove_reference_t<int&>, int>));
    CHECK((is_same_v<remove_reference_t<const int&&>, const int>));
    CHECK((is_same_v<remove_reference_t<double>, double>));
    CHECK((is_same_v<remove_const_t<const int>, int>));
    CHECK((is_same_v<remove_const_t<const int*>, const int*>));      // pointee const isn't top-level
    CHECK((is_same_v<remove_const_t<int* const>, int*>));
    CHECK(is_pointer_v<int*> && is_pointer_v<const char*> && is_pointer_v<int* const>);
    CHECK(!is_pointer_v<int> && !is_pointer_v<int&>);
    CHECK((is_same_v<conditional_t<true, int, double>, int>));
    CHECK((is_same_v<conditional_t<false, int, double>, double>));
    CHECK((is_same_v<conditional_t<(sizeof(long) == 8), long, long long>, long>));
    return judge::report();
}
```
