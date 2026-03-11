import sys
import os
import json

import argparse
import uuid  # Import uuid for run IDs
import threading  # Import threading for background task
import uvicorn  # Import uvicorn to run FastAPI

from datetime import datetime, timedelta
from pathlib import Path

# Removed START as it's implicit with set_entry_point
from langgraph.graph import END, StateGraph
from langchain_core.messages import HumanMessage
import pandas as pd
import akshare as ak

# --- Agent Imports ---
from src.agents.valuation import valuation_agent
from src.agents.state import AgentState
from src.agents.sentiment import sentiment_agent
from src.agents.risk_manager import risk_management_agent
from src.agents.technicals import technical_analyst_agent
from src.agents.portfolio_manager import portfolio_management_agent
from src.agents.market_data import market_data_agent
from src.agents.fundamentals import fundamentals_agent
from src.agents.researcher_bull import researcher_bull_agent
from src.agents.researcher_bear import researcher_bear_agent
from src.agents.debate_room import debate_room_agent
from src.agents.macro_analyst import macro_analyst_agent
from src.agents.macro_news_agent import macro_news_agent

# --- Logging and Backend Imports ---
from src.utils.output_logger import OutputLogger
from src.tools.openrouter_config import get_chat_completion
from src.utils.llm_interaction_logger import log_agent_execution, set_global_log_storage
from backend.dependencies import get_log_storage
from backend.main import app as fastapi_app
from src.utils.logging_config import setup_logger

# --- Import Summary Report Generator ---
try:
    from src.utils.summary_report import print_summary_report, build_summary_report, build_summary_payload
    from src.utils.agent_collector import store_final_state, get_enhanced_final_state

    HAS_SUMMARY_REPORT = True
except ImportError:
    HAS_SUMMARY_REPORT = False

# --- Import Structured Terminal Output ---
try:
    from src.utils.structured_terminal import print_structured_output

    HAS_STRUCTURED_OUTPUT = True
except ImportError:
    HAS_STRUCTURED_OUTPUT = False

# --- Initialize Logging ---
log_storage = get_log_storage()
set_global_log_storage(log_storage)
OUTPUT_LOGGER = OutputLogger()
sys.stdout = OUTPUT_LOGGER
logger = setup_logger("main_workflow")

# --- Run the Hedge Fund Workflow ---


def _append_debug_checkpoint(text: str):
    try:
        with open("logs/summary_debug_trace.txt", "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


def run_hedge_fund(
    run_id: str,
    ticker: str,
    start_date: str,
    end_date: str,
    portfolio: dict,
    show_reasoning: bool = False,
    num_of_news: int = 5,
    show_summary: bool = False,
    ollama_model: str | None = None,
    summary_json_out: str | None = None,
    summary_text_out: str | None = None,
):
    print(f"--- Starting Workflow Run ID: {run_id} ---")
    previous_ollama_model = os.environ.get("OLLAMA_MODEL")
    if ollama_model:
        os.environ["OLLAMA_MODEL"] = ollama_model
        print(f"--- Using run-specific Ollama model override: {ollama_model} ---")
    else:
        print(f"--- Using default Ollama model from environment: {previous_ollama_model or 'llama3'} ---")
    try:
        from backend.state import api_state

        api_state.current_run_id = run_id
        print(f"--- API State updated with Run ID: {run_id} ---")
    except Exception as e:
        print(f"Note: Could not update API state: {str(e)}")

    initial_state = {
        "messages": [],  # 初始消息为空
        "data": {
            "ticker": ticker,
            "portfolio": portfolio,
            "start_date": start_date,
            "end_date": end_date,
            "num_of_news": num_of_news,
            "ollama_model": ollama_model,
        },
        "metadata": {
            "show_reasoning": show_reasoning,
            "run_id": run_id,
            "show_summary": show_summary,
        },
    }

    def persist_summary_artifacts(final_state):
        if not HAS_SUMMARY_REPORT:
            return
        if not (show_summary or summary_json_out or summary_text_out):
            return

        store_final_state(final_state)
        enhanced_state = get_enhanced_final_state()
        summary_text = build_summary_report(enhanced_state)
        summary_payload = build_summary_payload(enhanced_state)
        summary_payload["run_id"] = run_id
        summary_payload["log_path"] = OUTPUT_LOGGER.filename

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_ticker = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in ticker)
        summary_dir = Path("reports") / "summaries"
        summary_dir.mkdir(parents=True, exist_ok=True)
        auto_summary_text_path = summary_dir / f"{safe_ticker}_summary_{timestamp}.txt"
        auto_summary_json_path = summary_dir / f"{safe_ticker}_summary_{timestamp}.json"

        auto_summary_text_path.write_text(summary_text + "\n", encoding="utf-8")
        auto_summary_json_path.write_text(
            json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if show_summary:
            with open("logs/last_summary_report.txt", "w", encoding="utf-8") as f:
                f.write(summary_text + "\n")

            summary_block = (
                "\n" + "#" * 96 + "\n"
                + "# FINAL SUMMARY REPORT".ljust(95) + "#\n"
                + "#" * 96 + "\n"
                + summary_text
                + f"\n\n[summary log file] {OUTPUT_LOGGER.filename}\n"
                + "[summary text file] logs/last_summary_report.txt\n"
                + f"[summary archive text file] {auto_summary_text_path}\n"
                + f"[summary archive json file] {auto_summary_json_path}\n"
            )
            OUTPUT_LOGGER.write(summary_block)

        if summary_text_out:
            summary_text_path = Path(summary_text_out)
            summary_text_path.parent.mkdir(parents=True, exist_ok=True)
            summary_text_path.write_text(summary_text + "\n", encoding="utf-8")

        if summary_json_out:
            summary_json_path = Path(summary_json_out)
            summary_json_path.parent.mkdir(parents=True, exist_ok=True)
            summary_json_path.write_text(
                json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    try:
        from backend.utils.context_managers import workflow_run

        with workflow_run(run_id):
            _append_debug_checkpoint(f"before_app_invoke:{run_id}")
            final_state = app.invoke(initial_state)
            _append_debug_checkpoint(f"after_app_invoke:{run_id}")
            OUTPUT_LOGGER.emit(f"--- Finished Workflow Run ID: {run_id} ---")

            if HAS_SUMMARY_REPORT and (show_summary or summary_json_out or summary_text_out):
                _append_debug_checkpoint(f"before_summary:{run_id}")
                persist_summary_artifacts(final_state)
                _append_debug_checkpoint(f"after_summary:{run_id}")

            if HAS_STRUCTURED_OUTPUT and show_reasoning:
                print_structured_output(final_state)
    except ImportError:
        _append_debug_checkpoint(f"before_app_invoke_importerror:{run_id}")
        final_state = app.invoke(initial_state)
        _append_debug_checkpoint(f"after_app_invoke_importerror:{run_id}")
        OUTPUT_LOGGER.emit(f"--- Finished Workflow Run ID: {run_id} ---")

        if HAS_SUMMARY_REPORT and (show_summary or summary_json_out or summary_text_out):
            _append_debug_checkpoint(f"before_summary_importerror:{run_id}")
            persist_summary_artifacts(final_state)
            _append_debug_checkpoint(f"after_summary_importerror:{run_id}")

        if HAS_STRUCTURED_OUTPUT and show_reasoning:
            print_structured_output(final_state)
        try:
            api_state.complete_run(run_id, "completed")
        except Exception:
            pass
    if ollama_model:
        if previous_ollama_model is None:
            os.environ.pop("OLLAMA_MODEL", None)
        else:
            os.environ["OLLAMA_MODEL"] = previous_ollama_model
    return final_state["messages"][-1].content


# --- Define the Workflow Graph ---
workflow = StateGraph(AgentState)

# Add nodes
workflow.add_node("market_data_agent", market_data_agent)
workflow.add_node("technical_analyst_agent", technical_analyst_agent)
workflow.add_node("fundamentals_agent", fundamentals_agent)
workflow.add_node("sentiment_agent", sentiment_agent)
workflow.add_node("valuation_agent", valuation_agent)
workflow.add_node("macro_news_agent", macro_news_agent)  # 新闻 agent
workflow.add_node("researcher_bull_agent", researcher_bull_agent)
workflow.add_node("researcher_bear_agent", researcher_bear_agent)
workflow.add_node("debate_room_agent", debate_room_agent)
workflow.add_node("risk_management_agent", risk_management_agent)
workflow.add_node("macro_analyst_agent", macro_analyst_agent)
workflow.add_node("portfolio_management_agent", portfolio_management_agent)

# Set entry point
workflow.set_entry_point("market_data_agent")

# Edges from market_data_agent to the five parallel agents
workflow.add_edge("market_data_agent", "technical_analyst_agent")
workflow.add_edge("market_data_agent", "fundamentals_agent")
workflow.add_edge("market_data_agent", "sentiment_agent")
workflow.add_edge("market_data_agent", "valuation_agent")
# macro_news_agent 也从 market_data_agent 并行出来
workflow.add_edge("market_data_agent", "macro_news_agent")

# Main analysis path (technical, fundamentals, sentiment, valuation -> researchers -> ... -> macro_analyst)
workflow.add_edge("technical_analyst_agent", "researcher_bull_agent")
workflow.add_edge("fundamentals_agent", "researcher_bull_agent")
workflow.add_edge("sentiment_agent", "researcher_bull_agent")
workflow.add_edge("valuation_agent", "researcher_bull_agent")

workflow.add_edge("technical_analyst_agent", "researcher_bear_agent")
workflow.add_edge("fundamentals_agent", "researcher_bear_agent")
workflow.add_edge("sentiment_agent", "researcher_bear_agent")
workflow.add_edge("valuation_agent", "researcher_bear_agent")

workflow.add_edge("researcher_bull_agent", "debate_room_agent")
workflow.add_edge("researcher_bear_agent", "debate_room_agent")

workflow.add_edge("debate_room_agent", "risk_management_agent")
workflow.add_edge("risk_management_agent", "macro_analyst_agent")

# Edges to portfolio_management_agent (汇聚点)
# macro_analyst_agent (end of main analysis path) and macro_news_agent (parallel news path)
# both feed into portfolio_management_agent.
# LangGraph will wait for both parent nodes to complete before running portfolio_management_agent.
workflow.add_edge("macro_analyst_agent", "portfolio_management_agent")
workflow.add_edge("macro_news_agent", "portfolio_management_agent")

# Final node
workflow.add_edge("portfolio_management_agent", END)

app = workflow.compile()

# --- FastAPI Background Task ---


def run_fastapi():
    print("--- Starting FastAPI server in background (port 8000) ---")
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000, log_config=None)


# --- Main Execution Block ---
if __name__ == "__main__":
    fastapi_thread = threading.Thread(target=run_fastapi, daemon=True)
    fastapi_thread.start()
    parser = argparse.ArgumentParser(description="Run the hedge fund trading system")
    parser.add_argument("--ticker", type=str, required=True, help="Stock ticker symbol")
    parser.add_argument(
        "--start-date",
        type=str,
        help="Start date (YYYY-MM-DD). Defaults to 1 year before end date",
    )
    parser.add_argument(
        "--end-date", type=str, help="End date (YYYY-MM-DD). Defaults to yesterday"
    )
    parser.add_argument(
        "--show-reasoning", action="store_true", help="Show reasoning from each agent"
    )
    parser.add_argument(
        "--num-of-news",
        type=int,
        default=20,
        help="Number of news articles to analyze for sentiment (default: 20)",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=100000.0,
        help="Initial cash amount (default: 100,000)",
    )
    parser.add_argument(
        "--initial-position",
        type=int,
        default=0,
        help="Initial stock position (default: 0)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Show beautiful summary report at the end",
    )
    parser.add_argument(
        "--hq",
        action="store_true",
        help="Use Ollama cloud high-quality model (deepseek-v3.1:671b-cloud)",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Override Ollama model for this run only",
    )
    parser.add_argument(
        "--summary-json-out",
        type=str,
        help="Optional path to write a machine-readable summary JSON artifact",
    )
    parser.add_argument(
        "--summary-text-out",
        type=str,
        help="Optional path to write the rendered summary text artifact",
    )
    args = parser.parse_args()
    current_date = datetime.now()
    yesterday = current_date - timedelta(days=1)
    end_date = (
        yesterday
        if not args.end_date
        else min(datetime.strptime(args.end_date, "%Y-%m-%d"), yesterday)
    )
    if not args.start_date:
        start_date = end_date - timedelta(days=365)
    else:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    if start_date > end_date:
        raise ValueError("Start date cannot be after end date")
    if args.num_of_news < 1:
        raise ValueError("Number of news articles must be at least 1")
    if args.num_of_news > 100:
        raise ValueError("Number of news articles cannot exceed 100")
    portfolio = {"cash": args.initial_capital, "stock": args.initial_position}
    main_run_id = str(uuid.uuid4())
    selected_model = args.model
    if args.hq and not selected_model:
        selected_model = "deepseek-v3.1:671b-cloud"

    result = run_hedge_fund(
        run_id=main_run_id,
        ticker=args.ticker,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        portfolio=portfolio,
        show_reasoning=args.show_reasoning,
        num_of_news=args.num_of_news,
        show_summary=args.summary,
        ollama_model=selected_model,
        summary_json_out=args.summary_json_out,
        summary_text_out=args.summary_text_out,
    )
    OUTPUT_LOGGER.emit("\nFinal Result:")
    OUTPUT_LOGGER.emit(str(result))
