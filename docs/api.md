# API reference

Public contracts are grouped by the responsibility that owns them.
See [Architecture](architecture.md) for dependency direction and composition.

## Everyday composition

::: nighthawk
    options:
      members:
        - function
        - run
        - scope
        - UNSET
        - Extend
        - Merge
        - NighthawkError

## Core execution

::: nighthawk.core.natural.definition
    options:
      members:
        - FunctionDefinition
        - get_function_definition

::: nighthawk.core.configuration
    options:
      members:
        - UnsetType

::: nighthawk.core.errors
    options:
      members:
        - NaturalParseError
        - ExecutionError
        - NameConflictError

::: nighthawk.core.calls
    options:
      members:
        - FunctionCall
        - FunctionCallHandler
        - FunctionCallError
        - function_call_scope
        - get_function_call

::: nighthawk.core.scoping
    options:
      members:
        - Scope
        - get_scope
        - run
        - scope

::: nighthawk.core.execution_reference
    options:
      members:
        - ExecutionReference

::: nighthawk.core.usage
    options:
      members:
        - ExecutionUsage
        - UsageMeter

::: nighthawk.core.model_selection
    options:
      members:
        - ModelSelection

::: nighthawk.core.step_executor
    options:
      members:
        - StepExecutor
        - SyncStepExecutor
        - AsyncStepExecutor
        - ConfigurableStepExecutor

::: nighthawk.core.step_definition
    options:
      members:
        - BlockKind
        - StepDefinition

::: nighthawk.core.step_context
    options:
      members:
        - StepContext
        - get_step_context

::: nighthawk.core.step_request
    options:
      members:
        - StepRequest
        - StepCandidate

::: nighthawk.core.step_outcome
    options:
      members:
        - Pass
        - Return
        - Break
        - Continue
        - Raise
        - StepOutcome
        - OutcomeKind

::: nighthawk.core.embedded
    options:
      members:
        - BindingWriter
        - execute_step
        - execute_step_async

::: nighthawk.core.bindings
    options:
      members:
        - infer_binding_types
        - validate_output_bindings
        - BindingError
        - BindingDeclarationError
        - BindingValidationError
        - MISSING_ANNOTATION

::: nighthawk.core.oversight
    options:
      members:
        - StepCommit
        - Rewrite
        - Accept
        - RejectOperation
        - AbortStep
        - Oversight
        - StepAbortedError
        - StepCommitDecision

::: nighthawk.core.lifecycle
    options:
      members:
        - StepLifecycle
        - StepCompleted
        - StepRaised
        - StepFailed
        - StepInterrupted
        - StepFinished
        - StepStarted
        - FailureStage

::: nighthawk.core.operation_contract
    options:
      members:
        - OperationRequest
        - OperationRejection
        - OperationRejectedError
        - OperationFinished
        - OperationError
        - TerminalOperationError
        - OperationDeliveryError

::: nighthawk.core.expression
    options:
      members:
        - evaluate_expression
        - evaluate_expression_async

::: nighthawk.core.assignment
    options:
      members:
        - AssignmentResult
        - assign_expression
        - assign_expression_async

::: nighthawk.core.environment
    options:
      members:
        - StepEnvironment

::: nighthawk.core.inspection
    options:
      members:
        - describe_value

::: nighthawk.core.testing
    options:
      members:
        - ScriptedExecutor
        - CallbackExecutor

## Harness

::: nighthawk.harness.context
    options:
      members:
        - AttributionNode
        - current_grant
        - current_capabilities
        - current_run_ledger
        - scope
        - current_executor_step_admission
        - terminate_descendant_capability

::: nighthawk.harness.grants
    options:
      members:
        - Capability
        - GrantEntryKind
        - Grant

::: nighthawk.harness.proposals
    options:
      members:
        - ExecutionIntent
        - DependencyDeclaration
        - CodeProposal

::: nighthawk.harness.perimeter
    options:
      members:
        - PerimeterEffectClass
        - EffectFootprint
        - PerimeterPolicy

::: nighthawk.harness.run_contract
    options:
      members:
        - RunIdentity
        - RunContract
        - ApprovedFunctionDefinition
        - ScaffoldComposition
        - AsyncScaffoldComposition

::: nighthawk.harness.runner
    options:
      members:
        - Runner

::: nighthawk.harness.run_handle
    options:
      members:
        - RunHandle

::: nighthawk.harness.resources
    options:
      members:
        - ResourceRequestKind
        - ResourceRequest
        - ResourceRejection
        - ResourceConsumption
        - ResourceAllocation
        - ExecutorStepAdmission
        - bounded_resource_allocation

::: nighthawk.harness.confinement
    options:
      members:
        - ConfinementRequest
        - ConfinementConfirmation
        - PerimeterConfinement
        - unconfirmed_perimeter_confinement

::: nighthawk.harness.audit
    options:
      members:
        - PerimeterAuditRecord
        - PerimeterAuditStore

::: nighthawk.harness.termination
    options:
      members:
        - TerminationRequest
        - TerminationCompletion
        - TerminationReport
        - TerminationMechanism
        - unavailable_termination_mechanism

::: nighthawk.harness.testing
    options:
      members:
        - InMemoryPerimeterAuditStore

::: nighthawk.harness.ledger
    options:
      members:
        - CapabilityDescriptor
        - ExecutorStepObservation
        - RunEntryEvent
        - SourceProvenanceClass
        - DefinitionKind
        - DefinitionMaterializationEvent
        - DispatchKind
        - DispatchEvent
        - DispatchOutcomeStatus
        - DispatchOutcomeEvent
        - NaturalExecutionOutcomeEvent
        - CapabilityInvocationEvent
        - PerimeterEffectAttemptEvent
        - PerimeterEffectCompletionEvent
        - ResourceExhaustionEvent
        - TraceAnomalyKind
        - TraceAnomalyEvent
        - TerminationRequestEvent
        - TerminationCompletionEvent
        - RunEndEvent
        - RunLedgerEvent
        - LedgerEntry
        - RunLedger

::: nighthawk.harness.dispatch
    options:
      members:
        - GeneratedDefinition
        - DispatchResult
        - GeneratedCallableReference
        - AsyncGeneratedCallableReference
        - dispatch
        - execute_code_capability

::: nighthawk.harness.generated
    options:
      members:
        - RetainedFunctionDefinition
        - RetainedFunction
        - retain_function

::: nighthawk.harness.evidence
    options:
      members:
        - EvidenceReference
        - resolve_evidence
        - CausalNode
        - RunAnalysis
        - select_subtree_entries
        - analyze_run

::: nighthawk.harness.errors
    options:
      members:
        - NaturalExecutionFailureKind
        - NaturalExecutionError
        - DispatchRejectionReason
        - DispatchRejectedError
        - GeneratedExecutionFailureKind
        - GeneratedExecutionError
        - GrantErrorReason
        - GrantError
        - RunRejectionReason
        - RunRejectedError
        - RunTerminalKind
        - RunFailedError
        - RunTerminationError
        - PerimeterEffectRejectionReason
        - PerimeterEffectRejectedError
        - TerminationFailureReason
        - TerminationError
        - CapabilityRejectedError



## Agent Parts

::: nighthawk.parts.values
    options:
      members:
        - BasicDataValue
        - TextWindow
        - TextSearchMatch
        - TextSearchPage
        - SequenceWindow
        - read_text_window
        - search_text
        - select_sequence_window

::: nighthawk.parts.voting
    options:
      members:
        - plurality
        - vote

::: nighthawk.parts.evidence
    options:
      members:
        - EvidenceItem
        - EvidenceQuery
        - EvidenceBounds
        - EvidenceProjection
        - project_evidence

::: nighthawk.parts.generation
    options:
      members:
        - FunctionRequest
        - propose_function

::: nighthawk.parts.sources
    options:
      members:
        - SourceCandidate
        - invoke_source
        - export_source
        - export_data

::: nighthawk.parts.procedures
    options:
      members:
        - select_procedure
        - selected_procedure
        - materialize_procedure

::: nighthawk.parts.procedures.catalog
    options:
      members:
        - ProcedureDescriptor
        - ProcedureQuery
        - ProcedureSelection
        - ProcedurePage
        - ProcedureCatalog
        - build_procedure_discovery

::: nighthawk.parts.learning
    options:
      members:
        - Narrative
        - EvidenceReferenceTuple
        - Lesson
        - ImprovementCandidate
        - LearningResult
        - derive_learning
        - format_candidate_review

::: nighthawk.parts.exploration
    options:
      members:
        - ExplorationTask
        - ExplorationResult
        - RetainedNote
        - explore
        - exploration_composition

::: nighthawk.parts.commands
    options:
      members:
        - CommandPolicy
        - CommandVerb
        - CommandRequest
        - CommandOutput
        - CommandExecutor
        - command_capability

::: nighthawk.parts.files
    options:
      members:
        - FilePatchPolicy
        - AddFileOperation
        - UpdateFileOperation
        - DeleteFileOperation
        - FilePatchOperation
        - FilePatchRequest
        - FilePatchOutcome
        - FileWriter
        - file_patch_capability

::: nighthawk.parts.web
    options:
      members:
        - WebSearchHit
        - WebFetchResult
        - WebSearchClient
        - WebFetchClient
        - build_research_web
        - WebRequestPolicy
        - RequestMethod
        - RequestHeader
        - WebRequest
        - WebResponse
        - WebRequestClient
        - web_request_capability

::: nighthawk.parts.images
    options:
      members:
        - ImageGenerationRequest
        - GeneratedImage
        - ImageGenerationClient
        - image_generation_capability

::: nighthawk.parts.workspace
    options:
      members:
        - FileSlice
        - DirectoryEntry
        - FileMatch
        - SearchHit
        - ImageReference
        - GrepBackend
        - build_inspect_workspace

::: nighthawk.parts.artifacts
    options:
      members:
        - StoredArtifactDescriptor
        - SaveArtifactRequest
        - LoadArtifactRequest
        - ArtifactStore
        - build_artifact_storage

::: nighthawk.parts.conversation
    options:
      members:
        - ContextItem
        - ContextPage
        - ContextQuery
        - ConversationAction
        - ConversationBindings
        - ConversationHistoryQuery
        - ConversationInput
        - ConversationPage
        - ConversationRecord
        - ConversationResult
        - ConversationStep
        - DataExport
        - FunctionDeclaration
        - FunctionSelection
        - SourceExport
        - SourceInvocation
        - SourceRetention
        - UserFacingBlock
        - choose_conversation_step
        - converse
        - perform_conversation_action

## Session integration

::: nighthawk.session
    options:
      members:
        - CellExecution
        - CellInput
        - CellMode
        - CellOutcome
        - CellOutput
        - CellOutputPage
        - CellStatus
        - ConversationCompositionBuilder
        - HistoryPage
        - HistoryRecord
        - IncludedSourceApproval
        - ManagedBackgroundWork
        - RatifiedFunctionSource
        - Session
        - SessionBackground
        - SessionBusyError
        - SessionClosedError
        - SessionError
        - SessionFunction
        - SessionHistory
        - SessionLookupError
        - SessionManager
        - SessionWaitContextError
        - build_conversation_composition
        - create_session

::: nighthawk.session.operator
    options:
      members:
        - filter_local_page
        - SessionLocals
        - SessionExecution
        - SessionOperations

::: nighthawk.session.operator_contract
    options:
      members:
        - Exposure
        - LocalQuery
        - LocalBindingObservation
        - LocalPage
        - BindingMutationResult
        - ExecutionObservation
        - InterruptionResult
        - BackgroundObservation
        - ExitResult
        - OperationKind
        - OperationDescriptor
        - HistoryFilter
        - PlainTextObservation
        - ReadLocalsRequest
        - InspectLocalRequest
        - DeleteLocalRequest
        - RenameLocalRequest
        - ReadExecutionRequest
        - InterruptExecutionRequest
        - ListBackgroundRequest
        - RegisterBackgroundRequest
        - InterruptBackgroundRequest
        - ExitRequest
        - HelpRequest
        - ListOperationsRequest
        - ReadHistoryRequest
        - SearchHistoryRequest
        - ReadEvidenceRequest
        - ReadTextRequest
        - OperatorRequest
        - OperatorResult

::: nighthawk.session.history
    options:
      members:
        - matches_history_record

::: nighthawk.session.controls
    options:
      members:
        - OperatorControl
        - is_operator_control

::: nighthawk.session.formatting
    options:
      members:
        - describe_operation
        - format_result
        - format_error

## Adapters

::: nighthawk.adapters.pydantic_ai.configuration
    options:
      members:
        - StepContextLimits
        - StepExecutorConfiguration
        - selected_configuration

::: nighthawk.adapters.pydantic_ai.executor
    options:
      members:
        - AgentStepExecutor
        - StepExecutionAgent
        - SyncExecutionAgent
        - AsyncExecutionAgent

::: nighthawk.adapters.pydantic_ai.scoping
    options:
      members:
        - ModelScope
        - get_model_scope
        - model_scope

::: nighthawk.adapters.pydantic_ai.context
    options:
      members:
        - ContextBuilder
        - DefaultContextBuilder

::: nighthawk.adapters.pydantic_ai.presentation
    options:
      members:
        - ToolResultRenderingPolicy
        - get_rendering_policy
        - rendering_scope

::: nighthawk.adapters.pydantic_ai.usage
    options:
      members:
        - execution_usage

::: nighthawk.adapters.pydantic_ai.harness
    options:
      members:
        - model_runner

::: nighthawk.adapters.pydantic_ai.model_outcome
    options:
      members:
        - ModelStepResponse
        - ModelOutcome

::: nighthawk.adapters.pydantic_ai.tools.declarations
    options:
      members:
        - ToolEntry

::: nighthawk.adapters.pydantic_ai.errors
    options:
      members:
        - ToolDeclarationError
        - ToolNameConflictError

::: nighthawk.adapters.pydantic_ai.models.base
    options:
      members:
        - BackendModelSettings

::: nighthawk.adapters.pydantic_ai.models.claude_code_settings
    options:
      members:
        - ClaudeCodeModelSettings
        - PermissionMode
        - SettingSource

::: nighthawk.adapters.pydantic_ai.models.claude_code_sdk
    options:
      members:
        - ClaudeCodeSdkModel
        - ClaudeCodeSdkModelSettings

::: nighthawk.adapters.pydantic_ai.models.claude_code_cli
    options:
      members:
        - ClaudeCodeCliModel
        - ClaudeCodeCliModelSettings

::: nighthawk.adapters.pydantic_ai.models.codex
    options:
      members:
        - CodexModel
        - CodexModelSettings
        - SandboxMode
        - ModelReasoningEffort

::: nighthawk.adapters.files
    options:
      members:
        - FileSystemWriter

::: nighthawk.adapters.http
    options:
      members:
        - HttpxWebFetchClient

::: nighthawk.adapters.artifacts
    options:
      members:
        - DirectoryArtifactStore
        - InMemoryArtifactStore

::: nighthawk.adapters.pydantic_ai.images
    options:
      members:
        - image_content
