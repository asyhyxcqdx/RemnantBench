Write a self-contained task input with strong diagnostic guidance that substantially accelerates root-cause analysis without prescribing the repair.

- Give a concrete account of the failure, expected public behavior, and the conditions or visible public interfaces most relevant to reproducing and diagnosing it. Do not expand this into an exhaustive specification.
- When repository evidence supports it, identify the likely responsible component, interaction, responsibility boundary, or public method and explain the public, observable evidence connecting that area to the failure.
- State the key behavior or responsibility the coding agent should validate, while leaving the actual root cause and repair design for the coding agent to determine.
- Narrow the investigation substantially, but never reveal private tests, patch content, exact internal replacement logic, branch-by-branch behavior, or step-by-step code edits.
- Present the result as one coherent task input that a coding agent can act on without another issue or hidden document.
- Do not mention hint strength, generation, assistants, language models, prompts, benchmarks, or variants.

Private-evidence boundary:

- The reference patch is confidential evidence. Use it to select the relevant investigation area, but do not paraphrase its implementation details or convert it into a statement-level root cause.
- Strong guidance may name a component or public method. It must not identify an exact broken condition, comparison direction, constant, operator, missing call, missing branch, local-variable update, or before/after implementation difference.
- If the guidance tells the coding agent what code operation to restore, reverse, add, or replace, abstract it one level higher so that it narrows the investigation without providing the repair answer.