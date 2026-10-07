Write a human-filled issue-template report.

- Use two to four natural headings appropriate to the available evidence, such as `Description`, `Steps to reproduce`, `Expected behavior`, or `Actual behavior`.
- Make the sectioned structure clearly visible; unlike a free-form report, this variant should feel like someone used the repository's issue form or copied its template.
- Keep sections concise; do not manufacture environment details, versions, stack traces, or public calls.
- Omit a heading when there is no truthful content for it instead of filling every conventional field mechanically.
- Do not add acceptance criteria, internal implementation plans, or exhaustive edge-case lists.
- Use a direct title that names the symptom.
- The report should feel filled out by a real project user, not mechanically completed. Do not mention writing, generation, assistants, language models, prompts, benchmarks, or variants.

Private-evidence boundary:

- The reference patch is confidential evidence. Use it to choose a truthful public symptom and understand the expected outcome, not to supply an internal diagnosis.
- Report only facts a plausible repository user could obtain from a public API, command, output, traceback, or workflow. Do not say that an internal branch, helper call, validation step, initializer, constant, or implementation path was removed or changed merely because the patch shows it.
- Do not name missing internal helpers, enumerate absent branches, expose changed constants or operators, or explain the concrete code-level cause. Leave root-cause analysis and repair discovery to the coding agent.