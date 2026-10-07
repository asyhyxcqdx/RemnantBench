Write a self-contained task input with light diagnostic guidance that helps the coding agent understand the problem and choose a useful starting point.

- Describe the observed behavior and expected public behavior clearly enough to understand the task without another issue or document.
- Use only the simplest truthful public context needed for that understanding; an exhaustive reproduction and a complete list of edge cases are not required.
- Add exactly one subtle, evidence-supported clue about a relevant invariant, input/output relationship, lifecycle transition, or boundary condition.
- Let the clue reduce uncertainty about where to begin, but do not name the responsible component or method, claim a root cause, or suggest a fix.
- Keep the task input concise and natural; it must not be a bare hint that depends on unseen context.
- Do not mention hint strength, generation, assistants, language models, prompts, benchmarks, or variants.

Private-evidence boundary:

- The reference patch is confidential evidence. Use it to select a truthful clue, but do not paraphrase its implementation details or convert it into a root-cause statement.
- Keep the clue at the public behavioral boundary. Do not name internal symbols, missing calls or branches, changed constants or operators, or a concrete code operation to restore.
- If the clue would tell the coding agent what implementation step to perform, abstract it to the observable invariant or input/output relationship instead.