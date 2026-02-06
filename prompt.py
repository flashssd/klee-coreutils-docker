prompt = """Above is a code coverage report. Hyphens (-) mark non-executable lines—such as comments or whitespace—that are excluded from coverage calculations. Hash marks (#####) are red flags, indicating executable lines that were never exercised by your tests and therefore need attention. Numeric values indicate successful coverage, showing how many times each line was executed. This helps verify that your logic is being exercised and can reveal performance hotspots or under-tested edge cases.
Provide input that produces the same coverage report shown above. Only provide the input. Do not include any additional commentary."""

prompt_symbolic_executor = """Act as a symbolic executor. For the program above, generate a set of inputs that maximizes line coverage.

Provide the inputs as a single list (e.g., [1, "a", "c"]). Output only the list of inputs. Do not include any additional commentary."""

prompt_target_uncovered = """Above is a cumulative code coverage report aggregated across multiple inputs. Hyphens (-) denote non-executable lines—such as comments or whitespace—that are excluded from coverage calculations. Hash marks (#####) flag executable lines that were never exercised by any test input and therefore require attention.
Plus marks (+) indicate successful coverage, meaning the line was executed by at least one prior input.
Now, act as a symbolic executor. For the program above, generate a set of inputs that specifically target execution of the uncovered lines marked with #####.
Provide the inputs as a single list (for example, [1, "a", "c"]). Return only the input list and no additional commentary."""
