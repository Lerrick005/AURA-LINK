"""Smoke test for the runtime engine and tool registry."""
import sys
sys.path.insert(0, ".")

from app.modules.tools.echo import register_echo_tool
from app.modules.runtime.registry import get_tool_registry
from app.modules.runtime.decision import decide_next_step, RunState
from app.modules.agents.schemas import AgentConfig, AgentLimits

def main():
    print("1. Registering tools...")
    register_echo_tool()
    
    print("2. Fetching registry and executor...")
    registry = get_tool_registry()
    executor = registry.executor_for("echo")
    print(f"   -> Executor found: {executor.__name__}")
    
    print("3. Validating input schema...")
    registry.validate_input("echo", {"message": "hello auralink"})
    print("   -> Validation passed!")
    
    print("4. Testing pure decision logic (initial state)...")
    limits = AgentLimits(max_steps=10, max_tool_calls=5, max_seconds=60, max_cost_usd=1.0)
    config = AgentConfig(
        instructions="You are a helpful assistant.",
        model="gpt-4o-mini",
        limits=limits,
        expected_terminal_tool=None,
        require_verified_terminal=False
    )
    
    state = RunState(
        steps=(),
        iteration_count=0,
        tool_call_count=0,
        cost_usd=0.0,
        elapsed_seconds=0.0,
        consecutive_rejections=0,
        nudge_issued=False,
        config=config,
        terminal_tool_status="not_run",
        pending_proposal_count=0,
        last_step_type=None
    )
    
    decision = decide_next_step(state)
    print(f"   -> Decision: kind='{decision.kind}'")
    
    print("\n✅ Smoke test PASSED! Engine components are wired correctly.")

if __name__ == "__main__":
    main()
