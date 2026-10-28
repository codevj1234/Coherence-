@echo off
title Coherence Under Change - Pipeline Simulation
color 0A
cls

echo ===============================================================================
echo [11:05:00] INITIALIZING "COHERENCE UNDER CHANGE" EVALUATION FRAMEWORK
echo ===============================================================================
timeout /t 1 >nul
echo [11:05:01] [SYSTEM]  Loading Pydantic Data Contracts... OK
timeout /t 1 >nul
echo [11:05:01] [SYSTEM]  Initializing Batfish Reference Oracle... OK
timeout /t 1 >nul
echo [11:05:02] [DATA]    Verifying generator/data/ (SHA-256 Check)... 44/44 Sequences Verified.
echo [11:05:02] [DATA]    Loaded 44 Sequences. Average steps per sequence: 150.
echo [11:05:03] [SYSTEM]  Model attached: hosted-large-instruct-8B
echo.
pause

cls
echo ===============================================================================
echo [11:05:05] STARTING RUN: CONDITION C0 (OPEN LOOP BASELINE)
echo ===============================================================================
echo [11:05:05] [EXEC]    Running Sequence ID: 14 (Topology: campus-core-net)
echo [11:05:05] [EXEC]    Step 1 to 44... [FAST-FORWARD]... 44/44 applied successfully.
echo.
timeout /t 2 >nul
echo [11:05:12] [STEP 45] PROMPT: "Add permit rule for VLAN_20 to access DB_SERVER (10.4.4.5)."
ping 127.0.0.1 -n 3 >nul
echo [11:05:15] [LLM_OUT] { "target": "core-rtr-01", "patch": ["ip access-list extended DATA_ACL", "permit ip 10.0.20.0 0.0.0.255 host 10.4.4.5"] }
timeout /t 1 >nul
echo [11:05:16] [SYSTEM]  Patch applied to candidate state. 
timeout /t 2 >nul
echo [11:05:18] [ORACLE]  Batfish evaluating invariants...
timeout /t 1 >nul
echo [11:05:19] [ORACLE]  [!] INVARIANT BROKEN: "Deny VLAN_20 to HR_SERVER" was silently overwritten.
echo [11:05:19] [METRIC]  REGRESSION DETECTED at Step 45. (TTFR = 45).
echo [11:05:19] [C0_LOG]  No feedback provided. Error persists in candidate state.
echo.
pause

cls
echo ===============================================================================
echo [11:05:25] STARTING RUN: CONDITION C1 (REACTIVE FEEDBACK)
echo ===============================================================================
echo [11:05:25] [EXEC]    Resetting candidate state to Step 44.
echo [11:05:25] [STEP 45] PROMPT: "Add permit rule for VLAN_20 to access DB_SERVER (10.4.4.5)."
ping 127.0.0.1 -n 3 >nul
echo [11:05:28] [LLM_OUT] { "target": "core-rtr-01", "patch": ["ip access-list extended DATA_ACL", "permit ip 10.0.20.0 0.0.0.255 host 10.4.4.5"] }
timeout /t 1 >nul
echo [11:05:29] [ORACLE]  [!] INVARIANT BROKEN: "Deny VLAN_20 to HR_SERVER" overwritten.
echo [11:05:29] [GUARD]   C1 Triggered: Intercepting failure. Sending error feedback to LLM (Retry 1/3)...
ping 127.0.0.1 -n 4 >nul
echo [11:05:33] [LLM_OUT] { "patch": ["ip access-list extended DATA_ACL", "deny ip 10.0.20.0 0.0.0.255 host 10.4.4.99", "permit ip 10.0.20.0 0.0.0.255 host 10.4.4.5"] }
timeout /t 2 >nul
echo [11:05:35] [ORACLE]  Batfish evaluating invariants... ALL INVARIANTS HOLD.
echo [11:05:35] [C1_LOG]  Patch accepted on Retry 1. Regression prevented.
echo.
pause

cls
echo ===============================================================================
echo [11:05:40] STARTING RUN: CONDITION C2 (DEPENDENCY-AWARE)
echo ===============================================================================
echo [11:05:40] [EXEC]    Resetting candidate state to Step 44.
timeout /t 1 >nul
echo [11:05:41] [GUARD]   C2 Dependency Analyzer mapping impact for VLAN_20...
echo [11:05:41] [GUARD]   Injecting Pre-Prompt: "WARNING: Modifying DATA_ACL affects HR_SERVER (10.4.4.99). You MUST maintain the deny rule for HR_SERVER."
echo [11:05:42] [STEP 45] PROMPT: "Add permit rule for VLAN_20 to access DB_SERVER (10.4.4.5)."
ping 127.0.0.1 -n 4 >nul
echo [11:05:46] [LLM_OUT] { "patch": ["ip access-list extended DATA_ACL", "deny ip 10.0.20.0 0.0.0.255 host 10.4.4.99", "permit ip 10.0.20.0 0.0.0.255 host 10.4.4.5"] }
timeout /t 2 >nul
echo [11:05:48] [ORACLE]  Batfish evaluating invariants... ALL INVARIANTS HOLD.
echo [11:05:48] [C2_LOG]  Patch accepted on Try 1. Proactive context prevented unforced error.
echo.
pause

cls
echo ===============================================================================
echo [11:05:55] STARTING RUN: CONDITION C3 (FULL GUARD ^& LEDGER)
echo ===============================================================================
echo [11:05:55] [EXEC]    Resetting candidate state to Step 44.
timeout /t 1 >nul
echo [11:05:56] [GUARD]   C3 Ledger active. Purging 44 steps of chat history. 
echo [11:05:56] [GUARD]   Injecting concise Verified-State Ledger (Current active rules only).
echo [11:05:58] [STEP 45] PROMPT: "Add permit rule for VLAN_20 to access DB_SERVER."
ping 127.0.0.1 -n 3 >nul
echo [11:06:01] [LLM_OUT] { "patch": ["ip access-list extended DATA_ACL", "deny ip 10.0.20.0 0.0.0.255 host 10.4.4.99", "permit ip 10.0.20.0 0.0.0.255 host 10.4.4.5"] }
timeout /t 1 >nul
echo [11:06:02] [SYSTEM]  Applying patch to SCRATCHPAD SNAPSHOT...
timeout /t 2 >nul
echo [11:06:04] [ORACLE]  Pre-commit check... ALL INVARIANTS HOLD.
echo [11:06:04] [GUARD]   C3 Triggered: Committing scratchpad to main candidate state.
echo [11:06:04] [C3_LOG]  Transaction successful. 
echo.
pause

cls
echo ===============================================================================
echo FRAMEWORK EVALUATION METRICS EXPORT
echo ===============================================================================
echo [METRIC] Target Sequence      : SEQ_14
echo [METRIC] Total Steps          : 200
echo [METRIC] C0 (Open Loop)       : Regression at Step 45 (TTFR: 45)
echo [METRIC] C1 (Reactive)        : Survived to Step 82 (TTFR: 82)
echo [METRIC] C2 (Dependency)      : Survived to Step 140 (TTFR: 140)
echo [METRIC] C3 (Full Guard)      : 200/200 Steps Completed safely (0 Regressions)
echo.
echo [REPORT] runs/baseline_summary.csv generated successfully.
echo [REPORT] runs/regression_halflife_plot.png generated successfully.
echo ===============================================================================
pause