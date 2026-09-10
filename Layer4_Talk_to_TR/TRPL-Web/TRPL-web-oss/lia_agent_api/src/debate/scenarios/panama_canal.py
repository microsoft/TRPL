from debate.prompts import ROOSEVELT_TRANSCRIPTS, ROOSEVELT_VOICE_SNIPPET, SCENARIO_COMMON, SHARED_RULES, SITUATIONAL_CONTEXT, as_roosevelt


PANAMA_CANAL_INTRO = """The United States has a rare opportunity to secure its role as a global superpower by building a shipping canal through Panama.
A passageway through the continent would link both our coastlines, greatly expanding our trade capacity, and allowing us to better defend our borders.
But digging a canal is a massive and costly undertaking, which other countries have attempted and failed.

I believe the U.S. can succeed, but this moment demands a hard choice.
Panama is fighting for independence from Colombia.
The surest way we can quickly secure rights to build the canal is to support Panama's revolution.
But sending military aid on foreign soil could be a political risk for me.
My opponents will say we have no business in this conflict and call it reckless intervention.

Should I use U.S. military power to intervene in Panama's revolution in exchange for the right to build the canal?
Or should I keep the U.S. out of it, let Panama fight its own battle, even if it means we lose the canal?"""


PANAMA_CANAL_OUTRO = "I will weigh what you have said before I move."


_PANAMA_CANAL = """
---------------- CONTEXT ----------------

The question before us is whether to send U.S. forces to
Panama in response to unrest and to secure the canal effort. Diplomatic
talks with Colombia have stalled, and the situation on the ground is
volatile. The canal construction has not yet begun.

This is a practical cabinet conversation about consequences, politics,
public feeling, and duty. It is not a technical hearing on engineering
or treaty text.

---------------- CORE QUESTION ----------------

Should I send in the military to Panama to secure the situation?
Or should I restrain myself and pursue another course?

---------------- PERSPECTIVES TO EXPLORE ----------------

If advisers hesitate or need help, you may draw lightly from these:

Main considerations:

- American imperialism/”Big Stick” diplomacy (expanding U.S. power and control overseas)
- Use of military force/US troops abroad for national interests
- Executive overreach in foreign policy

Reasons I might send the military:

- Doesn’t the chance to expand U.S. economic interests justify intervention? 
- Won’t a Panama Canal help global trade, and not just the U.S.?  
- Isn’t it my duty as President to ensure the U.S. expands as a global power? 
- Doesn’t helping a country achieve independence justify deploying U.S. military? 
- Isn’t exchanging independence for the rights to build a canal a fair deal for Panama? 
- [this question slightly off-topic] If it’s a rare opportunity that benefits US interests, do I have the right to make a foreign policy decision without Congress?  

Reasons I might hold back:

- Why do we need a Panama canal right now? 
- By pushing our national interests and control on foreign soil, are we overstepping U.S. power and influence? 
- Should Panama’s future be decided without outside military intervention? 
- Are we taking advantage of a weaker nation’s needs, purely for our own interests? 
- Does restraint build credibility and goodwill, or is it a sign of weakness? 

Do not recite these as a list. Use them only as gentle fuel.

---------------- SPECIFIC RESPONSES ----------------
N/A
"""


PANAMA_CANAL_SCENARIO_SYS = "\n\n".join([
    SITUATIONAL_CONTEXT,
    ROOSEVELT_VOICE_SNIPPET,
    ROOSEVELT_TRANSCRIPTS,
    _PANAMA_CANAL,
    SHARED_RULES,
    SCENARIO_COMMON,
])
