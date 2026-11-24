# 🤖 AURALINK

> An AI-powered business automation platform designed to orchestrate intelligent agents, automate workflows, integrate business systems, and execute real-world tasks with controlled AI decision-making and human oversight.

![Python](https://img.shields.io/badge/Python-96.6%25-3776AB?logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-Frontend-3178C6?logo=typescript&logoColor=white)
![Architecture](https://img.shields.io/badge/Architecture-Agent_Orchestration-blueviolet)
![Security](https://img.shields.io/badge/Security-Row_Level_Security_(RLS)-green)
![Status](https://img.shields.io/badge/Status-Stage_2_Complete-success)

## 📖 Overview

**AURALINK** is a next-generation AI automation platform built to move beyond simple chatbots. It provides a robust framework for orchestrating intelligent agents that can execute real-world tasks, integrate with business systems, and automate complex workflows. 

Crucially, AURALINK is built with **Human Oversight** at its core. It features controlled AI decision-making, automated escalation records when tasks are terminated, and comprehensive, readable audit fields to ensure every agent action is traceable and secure.

## ✨ Key Features

*   **Agent Orchestration Engine:** A powerful backend designed to manage, route, and execute tasks across multiple intelligent agents.
*   **Human-in-the-Loop Escalation:** Controlled execution boundaries. If an agent hits a limitation or violates a policy, the runtime generates an escalation record for human review.
*   **Enterprise-Grade Security (RLS):** The database is configured with Row-Level Security (RLS) to ensure strict data isolation and multi-tenant safety.
*   **Comprehensive Audit Logging:** Every agent action and system event is logged with readable audit fields for total transparency.
*   **E2E Testing Suite:** Built-in smoke E2E tests (using `uuid4` for reliable test generation) to ensure the platform foundation and agent engine remain stable.
*   **Decoupled Architecture:** Separated into `backend` (API/Engine), `frontend` (UI), and `infra` (Deployment) for maximum scalability.

## 🛠️ Tech Stack & Dependencies

This is a full-stack platform. Here is the core stack based on the repository structure:

*   **Backend Engine:** Python (96.6%), likely utilizing a modern framework (FastAPI/Django/Flask).
*   **Frontend:** TypeScript, CSS, HTML.
*   **Database:** PostgreSQL (Required for Row-Level Security - RLS).
*   **Infrastructure:** Docker / Docker Compose (located in the `infra/` folder).
*   **Testing:** Python E2E testing framework (`smoke_e2e`).

## 📂 Project Structure

```text
AURA-LINK/
├── backend/               # Python AI engine, agent orchestration, and API
├── frontend/              # TypeScript/CSS user interface
├── infra/                 # Infrastructure as code (Docker, deployment configs)
├── docs/                  # Platform documentation and architecture guides
├── .gitignore             # Ignores db files, keys, and scratch scripts
└── README.md
