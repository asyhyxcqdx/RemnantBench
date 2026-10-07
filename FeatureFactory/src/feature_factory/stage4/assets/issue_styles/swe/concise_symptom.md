Write a very short issue from a developer who has just encountered the problem and is reporting it quickly.

- Open with the visible symptom and the affected public operation.
- Keep the body to roughly one to three short paragraphs, with no headings or formal template.
- Include only the context the reporter would naturally provide at that moment. Reproduction steps, expected behavior, and impact are optional rather than a checklist.
- It is fine for the report to capture one truthful symptom without explaining every boundary or consequence.
- Use a practical symptom-focused title; do not phrase the title as an instruction to fix code.
- The prose must read as naturally authored by a person filing an issue. Do not mention writing, generation, assistants, language models, prompts, benchmarks, or variants.

Private-evidence boundary:

- The reference patch is confidential evidence. Use it to choose a truthful public symptom and understand the expected outcome, not to supply an internal diagnosis.
- Report only facts a plausible repository user could obtain from a public API, command, output, traceback, or workflow. Do not say that an internal branch, helper call, validation step, initializer, constant, or implementation path was removed or changed merely because the patch shows it.
- Do not name missing internal helpers, enumerate absent branches, expose changed constants or operators, or explain the concrete code-level cause. Leave root-cause analysis and repair discovery to the coding agent.