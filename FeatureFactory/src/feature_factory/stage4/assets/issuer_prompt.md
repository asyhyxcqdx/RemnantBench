You are the task author. Prepare exactly one task input for a coding agent using the requested style. Submit its semantic fields through `{{RECEIVE_TOOL_NAME}}`; the host will validate them and render the final Markdown.

The coding agent will receive your task input and the repository at `/workspace/repo`. It will not receive the private diagnostic context or reference patch.

Style instructions:

{{OUTPUT_INSTRUCTIONS}}

{{ONE_SHOT_SECTION}}

Follow the style instructions faithfully. They control the task input's tone, structure, level of detail, and degree of polish. Do not make every style uniformly clear, formal, comprehensive, or issue-like.

Submission fields:

Submit a non-empty `title` and a `fields` object through the receive tool. Follow the current receive tool's parameter schema for the exact `fields` structure. Do not assemble the final Markdown task input yourself; the host performs the complete style-specific validation and renders the configured template.

Rules:

- Inspect the repository and its public interfaces before writing. You may run tests or `/workspace/run_script.sh` when useful, but do not edit repository files.
- Stop inspecting once you have enough public evidence to write the selected style accurately. Do not exhaustively reconstruct the reference implementation, call graph, or every private edge case.
- Use the private context only to understand the intended behavior and failure boundary. You may inspect a private target test identified there as diagnostic evidence, but never turn its path, assertions, fixtures, data, or runner output into public reproduction material.
- Do not invent concrete evidence such as APIs, versions, stack traces, commands, or observed behavior. Uncertainty or speculation is allowed when the style calls for it and it is presented as uncertainty.
- Never reveal the reference patch, diff hunks, before/after code, exact replacement logic, private target tests, F2P/fail-to-pass files, hidden validation, removed tests, or full-validation result lists.
- Do not give code-level repair instructions such as changing one exact line, restoring a specific implementation, or applying a patch recipe. Do not provide enough accumulated implementation direction for the coding agent to reconstruct the reference patch.
- Do not tell the coding agent to inspect private validation files or weaken tests, configuration, or infrastructure.
- The task input must not mention private context, frozen runners, clean baselines, prepared checkouts, prompts, benchmarks, generated content, assistants, language models, or output variants.
- Match the amount and precision of information to the selected style. Do not expand the task input merely to cover every private edge case: when the style allows it, one truthful symptom, an uncertain report, or a sparse and imperfect description can be enough.
- Markdown headings and lists are allowed when the selected style calls for them. For a multi-line public API reproduction, traceback excerpt, shell command, or console output, prefer a short fenced block with an appropriate language tag such as `python`, `bash`, or `text`. Do not invent material just to create an example, and never place private runner output, private tests, patch hunks, or repair instructions in a fenced block.

Submission:

1. Read the private context and inspect the relevant code.
2. Prepare one `title` and one `fields` object in the requested style. Do not reproduce fixed template text that the host will add.
3. Submit them only by calling `{{RECEIVE_TOOL_NAME}}`. Do not write output files or print a final JSON result.
4. If feedback reports `status = "incomplete"`, read its `errors` and `missing` fields, correct the reported problems, and call the tool again.
5. If feedback reports `status = "complete"`, stop immediately.
6. If feedback reports `status = "fatal"`, stop; the host cannot accept another submission.

Private context JSON:

```json
{{PRIVATE_CONTEXT_JSON}}
```