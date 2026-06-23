Answers to your questions:

### Can LLM execute sprint?

Apparently, if sprint is a manageable piece of work (1 week of work with clear goals) the answer is YES. I've been working in more or less this mode for some other projects, for a few months now. The scaffolding I'm asking you to spec is basically my attempt at scaling this up and offloading some mundane task off my shoulders, while also opening it for other folks to explore.

However note that we are talking about sprints and not general research program - the later are genuinely hard and thats main reason for oversee committee.

### Quality

That's on oversee committee. If they decide to re-validate - they just create another task for revalidation. I'm not worried that LLM produce convincing results: in my practice they are not as bad, but ultimately if results don't reproduce they don't reproduce. The only real way to capture non-reproducible results is OC, the LLM either use existing skills or a waist of tokens.

Note: my strong pushback on total zero human intervention - the zero intervention is on individual research agents as they do their job (e.g. I don't want them bugging me if they can edit the file on the disk or run the script). Overall process definitely needs human intervention. Human oversee committee is the next step above one guy checking 5 claude code instances over and over again. I want human intervention to be controlled and to make agents day-to-day manage themselves.


### Zero human intervention for agents

Yes, this is my big worry - the fragility of the framework. I suspect it would be major pain point. Yet I know it is not impossible - see e.g. OpenClaw project.

### Self-editing Claude.md

Yep, thats a risk. I'm thinking maybe isolate non-whitelisted web search as agent without any access. Just ask "do research, generate report" without any tools. Then have some other agent review the report to sniff prompt injection, or something. For something truly serious, I'd add human gate, but hopefully we are not there yet.

### Resource manager

There will be guardrails around that: real money for now are basically tokens, and I am going to put hard constraints on the limit. For version 1 we can say zero tokens, and that would be it. The real uplift is managing compute which I desperately need, and agent runtime / usage, which is flat subscription fee but needs to be managed. I want it to be a part of design since this is a big pain for me to manage.

### Trifecta

It's not rediscovery - I want you to literally use it. I want you to research it and chekck how it is adopted to LLMs.

### Collapse org chart

N research agents definitely stay (one per sprint maybe). Program manager - something needs to generate new ideas, execute existing ideas and communicate with humans. Right now I'm literally opening new claude session with "review the results, propose ideas, lets brainstorm" every time sprint is finished. This can definitely be automated so that it's not one chat. I don't care if it is long-lived or simple session "Prepare for compaction -> /compact" preserves some content without reopening the session. I care about interface and results, but am flexible on specific implementation details. Resource manager - could be an agent or script, or some way for agents to schedule resources. I need to schedule resources as projects will be resource intensive and I don't want gridlocks. I used agents to some of these tasks, and they work better than direct scripts.

### Public web site

Web site is not *public* public - it can only be available internally to org.
