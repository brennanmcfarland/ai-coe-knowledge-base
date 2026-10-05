# Overview
Build a wizard-like web application that uses https://app.clickup.com/9017310967/v/o/s/90176814220 as a data source and walks a user through building an application.

The wizard should be language- and platform-agnostic and prescriptive as an educational resource, more like an interactive wiki than a coding assistant.

It should represent the project as a directed graph of nodes based on development tasks like procuring data, system design, security review, etc. Each node in the DAG should be clickable to navigate to that page. Each node should have a completion state that gets checked by the user and walking through the wizard is the process of navigating through all the frontier nodes and checking them off.

Reference the material at the clickup url for how to break down the graph into distinct nodes. Each page should contain a summary of information about that node/step in the wizard, with checklists where relevant. Navigating between pages/nodes should not reset the checklist. The summaries should include citations that open the source document on the right hand side.
# Design
- scraper component that searches the clickup space for resources and pulls them to a local store (NOT tracked in git - explicitly add the folder to .gitignore) - runnable via a scrape.sh script (rate limit it to it doesn't get debounced/cut off by the server)
- ontology-builder component that reads the documents + builds a graph of concepts - write a llama.cpp interface so it can use local models to scrape for content and abstract this all - runnable via a build_ontology.sh script. The graph should be defined as follows:
	- nodes can be TODO Think about this and what the frontend should look like (make it a wizard)
# Hard Requirements
- use Oauth and the REST API for Clickup and code a user login page to do so
- add graphify to the project as a dependency so you can navigate it more efficiently in the future
- the app should NOT rely on any backend services or push data anywhere public; it should ONLY serve data through its backend to its frontend and should be able to be run totally locally, with no authentication except to Clickup itself
- write the backend in python 3.14 and the frontend in vite 8.0. also install playwright 1.60 for internal testing (incl playwright MCP)

in particular, walk me through significant dependency decisions and give me some options so I can decide which ones to pull in, to be written in the plan