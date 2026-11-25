"""Assemble docs/uml/*.mmd into one printable HTML page (docs/uml/uml.html).

Open the page in Chrome and print to PDF (landscape, no margins), or let
`scripts/render_uml_pdf.md` describe the headless route. Mermaid is loaded from a pinned
CDN version, so rendering needs an internet connection once.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UML = ROOT / "docs" / "uml"
DIAGRAMS = [
    ("class_agents.mmd", "Class diagram 1 - agents and their collaborators",
     "The orchestrator is plain code; five LLM agents share one bounded loop (Agent.ask) and "
     "reach tools only through the ToolGateway, which checks ToolPolicy before every MCP call."),
    ("class_contracts.mmd", "Class diagram 2 - the typed contracts agents exchange",
     "Agents pass these Pydantic objects, never chat history. PlanDraft is validated into a "
     "PipelinePlan in code; DebugPatch has no field for tests or schemas."),
    ("seq_main_loop.mmd", "Sequence diagram 1 - the main agentic loop",
     "From request to approved load: plan, build every stage, validate, stage, human gate."),
    ("seq_build_stage.mmd", "Sequence diagram 2 - building one stage (test, debug, escalate)",
     "Three attempts per stage with the Debugger in between, then one replan, then a clean stop."),
    ("seq_quality_and_approval.mmd", "Sequence diagram 3 - quality routing and the approval gate",
     "Failed checks go back to the stage that produced the data; production needs a human token."),
]

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Pipeline Builder - UML</title>
<style>
  @page {{ size: 17in 11in; margin: 0.45in; }}
  body {{ font-family: Helvetica, Arial, sans-serif; color: #1D2027; margin: 0; }}
  section {{ page-break-after: always; break-after: page; padding: 0.1in 0.2in; }}
  section:last-child {{ page-break-after: auto; }}
  h1 {{ color: #861F41; font-size: 22pt; margin: 0 0 4pt; }}
  h2 {{ color: #861F41; font-size: 17pt; margin: 0 0 4pt; }}
  p {{ font-size: 11pt; margin: 0 0 10pt; max-width: 11in; }}
  .mermaid {{ display: flex; justify-content: center; }}
  section {{ break-inside: avoid; }}
  .mermaid svg {{ width: 100% !important; max-width: none !important; height: 7.9in !important; }}
</style></head><body>
{sections}
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: false, theme: "neutral",
    themeVariables: {{ fontFamily: "Helvetica, Arial, sans-serif", primaryColor: "#FFFFFF",
      primaryBorderColor: "#861F41", lineColor: "#5B6170" }},
    sequence: {{ useMaxWidth: true, mirrorActors: false }}, class: {{ useMaxWidth: true }} }});
  await mermaid.run();
  document.body.dataset.rendered = "yes";
</script></body></html>
"""


TITLE = ("<h1>Autonomous Data Pipeline Builder - UML</h1><p>ADS 5984 Agentic AI in Practice, "
         "Virginia Tech. Team: Harshil Agrawal, Siddharth Pal, Vasu Patel. Diagrams are drawn "
         "from the classes and methods in <code>src/adpb</code>.</p>")


def main() -> None:
    sections = []
    for i, (name, title, caption) in enumerate(DIAGRAMS):
        head = TITLE if i == 0 else ""  # the title shares the first diagram's page
        sections.append(f"<section>{head}<h2>{escape(title)}</h2><p>{escape(caption)}</p>"
                        f'<pre class="mermaid">{escape((UML / name).read_text())}</pre>'
                        f"</section>")
    (UML / "uml.html").write_text(PAGE.format(sections="\n".join(sections)))
    print(UML / "uml.html")


if __name__ == "__main__":
    main()
