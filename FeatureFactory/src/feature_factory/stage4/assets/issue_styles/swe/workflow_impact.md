Write a first-person or team-first issue centered on work the reporter was trying to complete when the behavior got in the way.

- Begin with the practical workflow or goal, then narrate where the observed result interrupts it and what consequence follows.
- Prefer a few natural paragraphs over headings, numbered reproduction steps, or an issue template.
- Explain only enough expected behavior to make the workflow impact understandable; do not turn the report into a complete contract.
- Keep the emphasis on blocked work, a forced workaround, or an incorrect workflow outcome rather than on a minimal technical reproduction.
- Keep implementation guesses out of the report.
- Use only public interfaces and facts that can be supported by the checked-out repository.
- The prose must sound like a human describing work they were trying to complete. Do not mention writing, generation, assistants, language models, prompts, benchmarks, or variants.

Private-evidence boundary:

- The reference patch is confidential evidence. Use it to choose a truthful public symptom and understand the expected outcome, not to supply an internal diagnosis.
- Report only facts a plausible repository user could obtain from a public API, command, output, traceback, or workflow. Do not say that an internal branch, helper call, validation step, initializer, constant, or implementation path was removed or changed merely because the patch shows it.
- Do not name missing internal helpers, enumerate absent branches, expose changed constants or operators, or explain the concrete code-level cause. Leave root-cause analysis and repair discovery to the coding agent.