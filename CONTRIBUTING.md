# Contributing

A small, single-maintainer project. Issues are welcome; pull requests are considered but
not promised. There is no support commitment.

## Before sending anything

Run the test and paste its output in the thread:

```
test.bat
```

It should end in `RESULT: PASS`. If it does not, that is the more interesting conversation.

## House rules

- **Disclose AI assistance.** If a tool wrote a substantial part of your contribution, say
  so in the pull request, or use an `Assisted-by:` trailer. `Co-authored-by` is reserved for
  people.
- **Keep the honest limits honest.** The README's "What it deliberately does not do" section
  is not marketing. If a change makes one of those claims false, fix it in the same commit.
- **Claims need evidence.** Prefer "hash matches the host value" to "works on my machine".
  Every number in the documentation should be traceable to a serial log.
- **One change per commit**, with a message that explains why rather than restating the diff.