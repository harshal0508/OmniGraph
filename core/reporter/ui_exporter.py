"""
core/reporter/ui_exporter.py
-----------------------------------------------------------------------------
Phase 5: Interactive UI Dashboard Generator.
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from core.schema import AnalysisReport
from core.arbiter.arbiter import get_patch

# Ultra-modern Linear/Vercel-inspired Dark Theme
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <title>OmniGraph Analysis</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.26.0/cytoscape.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { 
            font-family: 'Inter', sans-serif; 
            background-color: #000000; 
            color: #EDEDED; 
        }
        /* Subtle dot grid for the graph background */
        .graph-bg {
            background-image: radial-gradient(#333333 1px, transparent 1px);
            background-size: 24px 24px;
        }
        #cy { width: 100%; height: 100%; position: absolute; top: 0; left: 0; z-index: 1; }
        .graph-container { position: relative; height: calc(100vh - 4rem); }
        .sidebar { 
            height: calc(100vh - 4rem); 
            overflow-y: auto; 
            background: rgba(10, 10, 10, 0.95); 
            backdrop-filter: blur(12px);
            border-left: 1px solid #262626; 
            z-index: 10;
        }
        .finding-card { 
            cursor: pointer; 
            transition: all 0.2s ease; 
            background: #0A0A0A;
        }
        .finding-card:hover { 
            background: #171717; 
            border-color: #404040;
        }
        .finding-card.active { 
            background: #171717; 
            border-color: #3B82F6; 
            box-shadow: 0 0 0 1px #3B82F6;
        }
        pre, code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace; }
        
        /* Minimal scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #262626; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #404040; }
    </style>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        critical: '#EF4444',
                        warning: '#F59E0B',
                        info: '#3B82F6',
                        border: '#262626',
                        surface: '#171717'
                    }
                }
            }
        }
    </script>
</head>
<body class="overflow-hidden flex flex-col h-screen">

    <!-- Header -->
    <header class="h-16 border-b border-border flex items-center px-6 justify-between bg-[#000000] z-20 relative">
        <div class="flex items-center space-x-4">
            <div class="w-8 h-8 rounded-md bg-info/10 border border-info/30 flex items-center justify-center font-bold text-info shadow-[0_0_15px_rgba(59,130,246,0.2)]">
                ⬡
            </div>
            <div>
                <h1 class="text-[15px] font-semibold leading-tight text-white tracking-tight">OmniGraph</h1>
                <p class="text-[11px] text-[#A1A1AA] font-mono tracking-wider uppercase">Scan ID: <span id="scan-id" class="text-gray-400"></span></p>
            </div>
        </div>
        <div class="flex space-x-6 text-[13px] font-medium text-[#A1A1AA] bg-surface/50 border border-border px-4 py-1.5 rounded-full">
            <div class="flex items-center"><span class="w-2 h-2 rounded-full bg-critical mr-2 shadow-[0_0_8px_rgba(239,68,68,0.6)]"></span><span id="crit-count" class="text-white ml-1 font-bold"></span> &nbsp;Critical</div>
            <div class="flex items-center"><span class="w-2 h-2 rounded-full bg-warning mr-2"></span><span id="warn-count" class="text-white ml-1 font-bold"></span> &nbsp;Warnings</div>
            <div class="flex items-center text-gray-500 pl-4 border-l border-border"><span id="svc-count" class="text-white mr-1 font-bold"></span> Services Analyzed</div>
        </div>
    </header>

    <!-- Main Content -->
    <div class="flex flex-1">
        <!-- Graph Area -->
        <div class="flex-1 graph-container graph-bg">
            <div id="cy"></div>
            <!-- Legend overlay -->
            <div class="absolute bottom-6 left-6 bg-surface/80 p-3 rounded-lg border border-border backdrop-blur-md text-[12px] text-gray-300 flex space-x-5 z-20 shadow-2xl">
                <div class="flex items-center"><div class="w-3 h-3 rounded-full border-2 border-info mr-2 bg-info/10"></div>Service</div>
                <div class="flex items-center"><div class="w-3 h-3 rounded border-2 border-emerald-500 mr-2 bg-emerald-500/10"></div>Database</div>
                <div class="flex items-center"><div class="w-3 h-3 border-2 border-purple-500 rotate-45 mr-2 bg-purple-500/10"></div>Table</div>
                <div class="flex items-center"><div class="w-4 h-0.5 bg-critical shadow-[0_0_5px_rgba(239,68,68,0.8)] mr-2"></div>Race Edge</div>
            </div>
        </div>

        <!-- Sidebar / Findings -->
        <div class="w-[480px] sidebar shadow-2xl flex flex-col">
            <div class="px-5 py-4 border-b border-border bg-[#0A0A0A]/95 backdrop-blur sticky top-0 z-20 flex justify-between items-center">
                <h2 class="text-[13px] font-semibold text-gray-200">Analysis Results</h2>
                <span class="text-[11px] text-gray-500 font-mono">Select to map vectors</span>
            </div>
            <div id="findings-list" class="flex-1 p-4 space-y-3">
                <!-- Generated by JS -->
            </div>
        </div>
    </div>

    <script>
        // Data payload injected by Python backend
        const PAYLOAD = <!-- {{ OMNIGRAPH_PAYLOAD }} -->;
        
        // Populate header
        document.getElementById('scan-id').textContent = PAYLOAD.report.scan_id.substring(0, 8);
        document.getElementById('crit-count').textContent = PAYLOAD.report.critical_count;
        document.getElementById('warn-count').textContent = PAYLOAD.report.warning_count;
        document.getElementById('svc-count').textContent = PAYLOAD.report.total_services;

        // Init Cytoscape with high-end dark styling
        const cy = cytoscape({
            container: document.getElementById('cy'),
            elements: PAYLOAD.graph,
            style: [
                {
                    selector: 'node',
                    style: {
                        'label': 'data(name)',
                        'color': '#EDEDED',
                        'text-valign': 'bottom',
                        'text-halign': 'center',
                        'text-margin-y': '8px',
                        'font-size': '12px',
                        'font-family': 'Inter',
                        'font-weight': 500,
                        'text-background-opacity': 1,
                        'text-background-color': '#171717',
                        'text-background-padding': '4px',
                        'text-background-shape': 'roundrectangle',
                        'border-width': 2,
                        'overlay-opacity': 0
                    }
                },
                {
                    selector: 'node[type="service"]',
                    style: {
                        'background-color': '#0F172A', // Very dark blue
                        'border-color': '#3B82F6',     // Info blue
                        'shape': 'ellipse',
                        'width': '36px',
                        'height': '36px'
                    }
                },
                {
                    selector: 'node[type="database"]',
                    style: {
                        'background-color': '#064E3B', // Very dark green
                        'border-color': '#10B981',     // Emerald
                        'shape': 'roundrectangle',
                        'width': '40px',
                        'height': '40px'
                    }
                },
                {
                    selector: 'node[type="table"]',
                    style: {
                        'background-color': '#4C1D95', // Very dark purple
                        'border-color': '#8B5CF6',     // Violet
                        'shape': 'diamond',
                        'width': '32px',
                        'height': '32px'
                    }
                },
                {
                    selector: 'edge',
                    style: {
                        'width': 1.5,
                        'line-color': '#404040',
                        'target-arrow-color': '#404040',
                        'target-arrow-shape': 'triangle',
                        'curve-style': 'bezier',
                        'label': 'data(label)',
                        'font-size': '10px',
                        'font-family': 'ui-monospace, monospace',
                        'color': '#737373',
                        'text-rotation': 'autorotate',
                        'text-margin-y': '-8px',
                        'text-background-opacity': 1,
                        'text-background-color': '#000000',
                        'text-background-padding': '2px',
                        'arrow-scale': 1.2
                    }
                },
                // Highlight styles for active vectors
                {
                    selector: '.highlighted-node',
                    style: {
                        'border-width': 3,
                        'border-color': '#EF4444',
                        'box-shadow': '0 0 20px rgba(239, 68, 68, 0.8)'
                    }
                },
                {
                    selector: '.highlighted-edge',
                    style: {
                        'line-color': '#EF4444',
                        'target-arrow-color': '#EF4444',
                        'width': 3,
                        'z-index': 999,
                        'color': '#FCA5A5' // Lighter red text
                    }
                },
                {
                    selector: '.dimmed',
                    style: {
                        'opacity': 0.15
                    }
                }
            ],
            layout: {
                name: 'cose',
                padding: 70,
                nodeRepulsion: 6000,
                idealEdgeLength: 120,
                edgeElasticity: 100,
                animate: false
            }
        });

        // Render findings
        const list = document.getElementById('findings-list');
        
        PAYLOAD.report.findings.forEach((finding, idx) => {
            if (!finding.is_actionable) return;
            
            const card = document.createElement('div');
            card.className = 'finding-card p-5 rounded-xl border border-border relative overflow-hidden group';
            
            const isCrit = finding.severity === 'CRITICAL';
            const badgeColor = isCrit ? 'bg-critical/20 text-critical border-critical/30' : 'bg-warning/20 text-warning border-warning/30';
            const dotColor = isCrit ? 'bg-critical' : 'bg-warning';
            
            let aiPatchHtml = '';
            if (finding.llm_patch) {
                aiPatchHtml = `
                    <div class="mt-4 pt-4 border-t border-border">
                        <div class="font-semibold text-info text-[12px] mb-2 flex items-center tracking-wide">
                            <span class="mr-1.5">✨</span> AI ARBITER PATCH
                        </div>
                        <p class="text-[12px] text-gray-300 leading-relaxed mb-2"><span class="text-gray-500">Root Cause:</span> ${finding.llm_patch.root_cause}</p>
                        <div class="bg-surface border border-border rounded-lg p-2.5">
                            <code class="text-[11px] text-emerald-400 break-words">${finding.llm_patch.fix_recommendation}</code>
                        </div>
                    </div>
                `;
            } else if (finding.remediation_hint) {
                aiPatchHtml = `
                    <div class="mt-4 pt-4 border-t border-border">
                        <span class="text-gray-500 font-semibold text-[12px]">Suggested Fix:</span>
                        <p class="text-[12px] text-gray-300 mt-1">${finding.remediation_hint}</p>
                    </div>
                `;
            }
            
            // Format actors gracefully
            const a1 = finding.actor_1;
            const a2 = finding.actor_2;
            const tgt = finding.shared_target;
            const actors = a1 === a2 ? 
                `<span class="text-gray-200 bg-surface px-1.5 py-0.5 rounded border border-border">${a1}</span> <span class="text-gray-500 ml-1">(multi-replica)</span>` : 
                `<span class="text-gray-200 bg-surface px-1.5 py-0.5 rounded border border-border">${a1}</span> <span class="text-gray-600 px-1">✕</span> <span class="text-gray-200 bg-surface px-1.5 py-0.5 rounded border border-border">${a2}</span>`;
            
            card.innerHTML = `
                <!-- Hover gradient effect -->
                <div class="absolute inset-0 bg-gradient-to-br from-white/[0.02] to-transparent opacity-0 group-hover:opacity-100 transition-opacity"></div>
                
                <div class="relative z-10">
                    <div class="flex items-start justify-between mb-3">
                        <h3 class="font-semibold text-[14px] text-white flex items-center tracking-tight">
                            ${finding.collision_type}
                        </h3>
                        <span class="text-[10px] font-bold border px-2 py-0.5 rounded-full ${badgeColor}">
                            ${finding.severity}
                        </span>
                    </div>
                    
                    <div class="text-[12px] text-gray-400 space-y-2 font-mono">
                        <div class="flex items-center">
                            <span class="w-16 text-gray-500">Actors:</span>
                            <span class="flex-1">${actors}</span>
                        </div>
                        <div class="flex items-center">
                            <span class="w-16 text-gray-500">Target:</span>
                            <span class="text-purple-400 bg-purple-900/20 px-1.5 py-0.5 rounded border border-purple-500/30">${finding.shared_target}</span>
                        </div>
                    </div>
                    
                    ${aiPatchHtml}
                </div>
            `;
            
            card.addEventListener('click', () => {
                // Remove active class from all
                document.querySelectorAll('.finding-card').forEach(c => c.classList.remove('active'));
                card.classList.add('active');
                
                // Reset graph styles
                cy.elements().removeClass('highlighted-node highlighted-edge dimmed');
                
                // Highlight involved nodes
                const involvedNodes = cy.nodes().filter(n => 
                    n.id() === a1 || n.id() === a2 || n.id() === tgt
                );
                
                // Highlight involved edges (between actors and target)
                const involvedEdges = cy.edges().filter(e => {
                    const src = e.source().id();
                    const trg = e.target().id();
                    return (src === a1 && trg === tgt) || (src === a2 && trg === tgt);
                });
                
                // Dim everything else
                cy.elements().difference(involvedNodes).difference(involvedEdges).addClass('dimmed');
                
                // Add highlight styles
                involvedNodes.addClass('highlighted-node');
                involvedEdges.addClass('highlighted-edge');
                
                // Fit view to highlighted elements
                cy.animate({
                    fit: { eles: involvedNodes.union(involvedEdges), padding: 120 },
                    duration: 600,
                    easing: 'ease-in-out-cubic'
                });
            });
            
            list.appendChild(card);
        });

        // Click background to clear selection
        cy.on('tap', function(event){
            if(event.target === cy){
                document.querySelectorAll('.finding-card').forEach(c => c.classList.remove('active'));
                cy.elements().removeClass('highlighted-node highlighted-edge dimmed');
                cy.animate({ fit: { padding: 70 }, duration: 600, easing: 'ease-in-out-cubic' });
            }
        });
        
        // Initial fit
        cy.fit(undefined, 70);
    </script>
</body>
</html>"""


def _graph_to_cytoscape(graph: nx.MultiDiGraph) -> list[dict]:
    elements = []
    for node_id, data in graph.nodes(data=True):
        elements.append({
            "data": {
                "id": node_id,
                "name": data.get("name", node_id),
                "type": str(data.get("node_type", "unknown")).lower(),
                "replica_count": data.get("replica_count", 1)
            }
        })
    for u, v, key, data in graph.edges(data=True, keys=True):
        elements.append({
            "data": {
                "id": f"e_{u}_{v}_{key}",
                "source": u,
                "target": v,
                "label": data.get("edge_type", ""),
                "pattern": data.get("pattern", "")
            }
        })
    return elements


def _findings_to_dicts(report: AnalysisReport) -> dict:
    findings_data = []
    for f in report.findings:
        data = {
            **f.to_scrubbed_dict(),
            "suppressed":         f.suppressed,
            "suppression_reason": f.suppression_reason,
            "remediation_hint":   f.remediation_hint,
            "is_actionable":      f.is_actionable,
        }
        patch = get_patch(f)
        if patch:
            data["llm_patch"] = {
                "provider": patch.provider,
                "was_ensemble": patch.was_ensemble,
                "root_cause": patch.root_cause,
                "fix_recommendation": patch.fix_recommendation,
            }
        findings_data.append(data)
        
    return {
        "scan_id": report.scan_id,
        "critical_count": report.critical_count,
        "warning_count": report.warning_count,
        "total_services": report.total_services,
        "findings": findings_data
    }


def export_html_dashboard(report: AnalysisReport, graph: nx.MultiDiGraph, output_path: Path) -> None:
    payload = {
        "graph": _graph_to_cytoscape(graph),
        "report": _findings_to_dicts(report)
    }
    payload_json = json.dumps(payload, indent=2)
    html = _HTML_TEMPLATE.replace("<!-- {{ OMNIGRAPH_PAYLOAD }} -->", payload_json)
    output_path.write_text(html, encoding="utf-8")
