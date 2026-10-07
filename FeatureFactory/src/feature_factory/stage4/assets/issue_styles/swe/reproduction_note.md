Write a technical but natural issue whose main value is the smallest truthful public reproduction of the behavior.

- Describe the shortest truthful sequence that demonstrates the symptom through a public API, command, configuration surface, or workflow.
- When repository evidence supports an exact public call, a short fenced example is welcome; otherwise use prose steps and do not invent code.
- Make the reproduction visually easy to identify with concise steps or a short example, followed by the observed and expected results.
- Keep background narrative and general workflow impact secondary; the reproducible operation is the center of this report.
- Keep the report focused and omit private runner commands, tests, patch details, and implementation instructions.
- The result must resemble an issue submitted by a developer who reproduced a problem, never a generated test case. Do not mention assistants, language models, prompts, benchmarks, or variants.

Private-evidence boundary:

- The reference patch is confidential evidence. Use it to choose a truthful public symptom and understand the expected outcome, not to supply an internal diagnosis.
- Report only facts a plausible repository user could obtain from a public API, command, output, traceback, or workflow. Do not say that an internal branch, helper call, validation step, initializer, constant, or implementation path was removed or changed merely because the patch shows it.
- Do not name missing internal helpers, enumerate absent branches, expose changed constants or operators, or explain the concrete code-level cause. A minimal public reproduction demonstrates the failure; it must not also reveal its implementation-level cause. Leave root-cause analysis and repair discovery to the coding agent.