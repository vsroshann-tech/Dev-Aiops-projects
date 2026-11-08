from docs_agent.crew import DocsAgentCrew

def run():
    # Put your questions about the Roshan Observability Platform here
    inputs = {
        "question": "What is the Roshan platform, and why is it better than Prometheus by using VictoriaMetrics?"
    }
    
    print("🤖 Running Roshan Docs Agent with your query...")
    result = DocsAgentCrew().crew().kickoff(inputs=inputs)
    
    print("\n==============================")
    print("📌 AGENT RESPONSE:")
    print("==============================")
    print(result)

if __name__ == "__main__":
    run()
