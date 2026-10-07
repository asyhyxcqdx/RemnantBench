Write a conversational issue in the voice of a user who believes an established public behavior or familiar workflow has regressed.

- Organize the report around a before-versus-now contrast: what behavior the reporter relied on, what happens now, and why the difference matters.
- Do not invent a release number, upgrade event, change date, or personally observed known-good version. If the repository only establishes a contract, describe the current behavior as appearing to regress that established behavior.
- A little uncertainty about when or why the change happened is natural; do not overstate the root cause.
- Do not turn the report into generic workflow-impact prose: the apparent change from established behavior must remain central.
- Keep the structure conversational rather than using an acceptance-criteria checklist.
- The wording must look like an ordinary human regression report. Do not mention writing, generation, assistants, language models, prompts, benchmarks, or variants.

Private-evidence boundary:

- The reference patch is confidential evidence. Use it to choose a truthful public symptom and understand the expected outcome, not to supply an internal diagnosis.
- Report only facts a plausible repository user could obtain from a public API, command, output, traceback, or workflow. Do not say that an internal branch, helper call, validation step, initializer, constant, or implementation path was removed or changed merely because the patch shows it.
- Do not name missing internal helpers, enumerate absent branches, expose changed constants or operators, or explain the concrete code-level cause. Keep the before-versus-now contrast at the public behavior level and leave repair discovery to the coding agent.