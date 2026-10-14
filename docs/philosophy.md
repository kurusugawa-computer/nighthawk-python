# Philosophy

Nighthawk turns AI judgment into executable capabilities that can be composed, evaluated, and improved, expanding the work programs can carry out autonomously.
AI agents build these capabilities by combining natural-language judgment with Python computation and actions.

Ordinary Python and model libraries can also express this work.
Nighthawk must earn its place by making such programs easier to construct and change correctly, their results and effects easier to assess, and their overall complexity lower.
Concise syntax alone is insufficient justification.

This document guides long-term design.
The [specification](specification.md) defines supported behavior, [design drivers](design-drivers.md) connect application evidence to Core decisions, and the [roadmap](roadmap.md) records exploration.

## Agent authorship and accumulated capability

Design for AI agents as the authors and maintainers of programs.
They need to discover functions, understand their intent and contracts, compose them correctly, and revise them using evidence.
Names, signatures, types, descriptions, and useful failure reports support that work.
Humans may supply goals and acceptance criteria or assess consequences; program construction should not depend on human authorship.

The intended destination is a growing body of reusable capabilities: judgment criteria, operations, evaluators, and the functions connecting them.
Work on one task should make later tasks easier to accomplish.
Applications own how they retain, evaluate, and improve these capabilities; automatic learning or self-modification is not a Core obligation.

## Python composition as the foundation

Commit to Python functions and its ecosystem as the basis for composition.
Combine judgment, action, constraints, and feedback through ordinary calls and existing libraries.
Python defines execution structure; Natural supplies judgment within that structure, from a small interpretation to an extended investigation.
This commitment enables reuse while accepting dependence on Python.

Composition requires understandable effects, state, ordering, failures, and completion as well as compatible types.
Make those consequences available to authors and callers so they can choose appropriate boundaries and recovery.
Shared state and partial effects remain deliberate application design concerns.

## Judgment guided by evidence

Use Python where explicit rules suffice and Natural where execution requires interpretation of meaning or context.
Revisit that division as evidence, requirements, and model capabilities change.
Replacing a judgment with validated explicit rules can be an improvement; using Natural or making more model calls is not an objective.

Assess capabilities through observed results and effects.
Type validation establishes value contracts; it cannot establish the correctness of a judgment.
Tests, evaluators, and simulations supply evidence with limits determined by their coverage and accuracy.
Preserve evidence that connects an assessment to what was actually executed, and use failures to improve the composition.
Judge success against application goals and acceptable effects while accounting for inference cost and latency.

## A minimal, balanced Core

Core supplies the execution semantics and connection points needed to compose Natural and Python reliably.
Applications and reusable libraries own domain actions, policies, storage, environments, and improvement processes.

Begin with ordinary functions and existing libraries.
Adopt a Core capability when:

1. Distinct applications share the same execution need.
2. Application composition cannot adequately meet that need.
3. Its meaning and guarantees can be explained clearly.
4. Applications that do not use it incur little conceptual or configuration burden.

Minimize the total burden on authors, hosts, adapters, and maintainers, including complexity displaced into applications.
Provide usable connection points and verified examples for responsibilities assigned to hosts.
Evaluate both new features and Nighthawk itself by whether they help agents build, reuse, assess, and improve capabilities.
