Write a self-contained task input with diagnostic guidance that materially narrows the area the coding agent needs to investigate.

- Describe the failure and expected public behavior, using the shortest truthful public workflow or reproduction that makes the relevant boundary visible.
- Select the boundary condition that is most useful for diagnosis instead of enumerating every known edge case.
- Include a verified clue that narrows investigation to a subsystem, state transition, data-flow boundary, or interaction between public components, and briefly explain how that boundary relates to the symptom.
- Do not narrow the task to an exact code edit, quote private tests, expose a patch, or state an unverified root cause.
- Present the guidance as credible technical context, not as a repair recipe or a bare hint that requires another task description.
- Do not mention hint strength, generation, assistants, language models, prompts, benchmarks, or variants.

Private-evidence boundary:

- The reference patch is confidential evidence. Use it to choose a useful investigation boundary, but do not paraphrase its implementation details or convert it into a root-cause statement.
- You may narrow the investigation to a subsystem, state transition, data-flow boundary, or component interaction. Do not identify an exact broken statement, helper call, branch, comparison, constant, operator, or code operation to restore.
- If the guidance provides the repair answer instead of reducing the search area, abstract it one level higher before submitting.