Write a slightly informal, low-confidence issue from someone who noticed surprising behavior but is not sure whether it is a bug, a misunderstanding, or intended behavior.

- Lead with what seemed surprising. A natural question such as "is this expected?" is welcome, but do not force one.
- Preserve the reporter's uncertainty instead of converting the input into a confident diagnosis or polished bug specification.
- A partial reproduction, an implicit expectation, sentence fragments, a brief aside, or slightly imperfect organization are acceptable when they fit the evidence. Complete steps and formal expected/actual sections are not required.
- Provide some public context, but do not fill every information gap from private diagnostics or speculate through a list of possible root causes.
- Use a credible human title and voice; mild confusion is appropriate, manufactured drama is not. Do not mention writing, generation, assistants, language models, prompts, benchmarks, or variants.

Private-evidence boundary:

- The reference patch is confidential evidence. Use it to choose a truthful public symptom and understand the expected outcome, not to supply an internal diagnosis.
- Report only facts a plausible repository user could obtain from a public API, command, output, traceback, or workflow. Do not say that an internal branch, helper call, validation step, initializer, constant, or implementation path was removed or changed merely because the patch shows it.
- Uncertainty does not justify inspecting private evidence and then speculating that a particular helper, branch, initializer, constant, or call was removed. Do not explain the concrete code-level cause; leave root-cause analysis and repair discovery to the coding agent.