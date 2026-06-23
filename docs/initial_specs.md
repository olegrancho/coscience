The idea is to build a platform to coordinate work of LLM-based researchers

# Components

- Research program - general and frequently open-ended program for LLM-based researchers to focus on. Think: scientific problem researchers need to adress. E.g. cure cancer
- Results database - a database (on top of maybe filestore) where research results are stored. Artefacts are lightweight enough so that researchers could have 10-20 of artefacts in their context window without overloading it. The storage is hyperlinked, and each reasearch result is stored in such a was so that link to produced artefacts (e.g. codebase, figures, datasets) could be obtained. Related research results (e.g. previous results that were refuted/confirmed, or inspired current) are stored too. On top of the platform there is keyword / full text search + semantic layer: auto-generated keywords to assist with retrieval, and embedding-based semantic search. Results database is available for web search by humans (to read results, click through links etc) - so a website - and also there is API or MCP to work with it.
- Research sprint - a short and ideally self-contained research that can be reasonably executed by research agent in few days to a week. Should have well defined goals, plans. At the end of the sprint an artifact should be produced and submitted to RDB. Resources required for sprint should be speficied up front. Some artifacts may produce follow-up ideas.
- Research agent - individual LLM-based agent that execute research sprint. Should have access to tools that are required to execute research. The agent autonomously makes decisions that enable sprint execution, runs necessary experiments, solves problems and produces artefacts.
- Institute - a collection of resources and research agents to execute research program.
- Resources - resources available to institute include: money (could be spent to buy tokens etc), tokens themselves, GPU access time, compute time. Resources are needed to run experiments, and also to host research agents themselves.
- Resource manager - individual LLM-based agent that manages resources of institute and allocates resources to individual sprints
- Program manager - individual LLM-based agent that is responsible for research program. It summarizes output from individual research sprints, and proposes new research directions. Proposals are posted to research dashboard for approval by oversee committee.
- Research dashboard - general dashboard that contains cards for individual research program: sprints (with different status: approved, executing, canceled, done), and a way for committee members to communicate to program manager and each other.
- Oversee committee - committee of human stakeholders who oversee the research progress and are responsible for resource allocation and decisions on research program

# Implementation details

To avoid redundancy and minimize costs, research agents, resource managers and program managers should be implemented as individual Claude Code sessions. The sessions should be long-lived and should require zero human intervention to run. They should be equipped with necessary tools (access to servers, access to dashboard etc). Importantly - agents should have a capability to improve on what they are doing: individual Claude.md files should be appended with relevant information and trimmed periodically so that agents could learn from the process. Different version of research agents equipped with specific tools could be approved for project execution by oversee committee.

**Sandboxing**: ideally, agents should be reasonably sandboxed. Individual agent could only have access to 2 of 3 tools from the list:
a. Web search and non-whitelisted web data retrieval (crawling)
b. Access to private data and tools
c. Access to computational tools (ability to run scripts)
For example, agent can do web search and have access to private data and tool, but it will not have ability to run scripts. Alternatively, it can access data & tools and write scripts, but not general internet access to non-whitelisted sites.

Recommended format for some of results database - Open Knowledge Format (by Google).

# Typical workflow

General program is created externally. Program manager is initiated and asked to generate ideas for research sprints. These ideas are posted to research dashboard.
Oversee committee reviews these ideas. Some are rejected. Some are sent to program manager with extra questions or with requests to refine. Some are added by committee members. After several iterations committee approves sprints and allocates overall budget. Resource manager is informed about current resource pool, priorities, claims and allocations.
Program manager starts execution of these sprints. Individual research agents are initiated, and tasked with research and then execution. If some agent is requesting resource - it connects to resource manager, who either approves or puts on hold.
As sprints are executed, results are generated and updated to results database.
Program manager reviews results database and proposes new sprints.
Oversee committee reviews current dashboard to track progress. Committee proposes new sprints and approves/rejects/refines sprints proposed by program manager.
The process repeats until research program goal is achieved.

