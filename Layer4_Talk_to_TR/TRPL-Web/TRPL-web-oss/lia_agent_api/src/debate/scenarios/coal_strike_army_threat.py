# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from debate.prompts import ROOSEVELT_TRANSCRIPTS, ROOSEVELT_VOICE_SNIPPET, SCENARIO_COMMON, SHARED_RULES, SITUATIONAL_CONTEXT, as_roosevelt


COAL_STRIKE_ARMY_THREAT_INTRO = """My trusted advisors, we face a grave crisis.

For six months, the coal strike in Pennsylvania has been choking off our nation’s fuel supply.
150,000 coal miners demand safer working conditions and higher wages.
Mine operators won’t hear of it. They insist that giving into the union will raise the price of coal and spark more unrest.

Everyday, I hear of growing coal lines in our cities…
…as millions are without heat.

I summoned both sides to Washington DC to negotiate an agreement…
but those talks failed.

Now winter is closing in, and I will not stay idle as the people freeze!

One course has been put before me: I could threaten to send in the army to take over the mines.
Not to fire a shot — but to make plain that the nation will not be held hostage.

Before I brandish such power, I want your counsel.

To get coal to the people, should I threaten a military takeover of the mines?

Or should I hold back?
"""


COAL_STRIKE_ARMY_THREAT_OUTRO = (
    "I will weigh what you have said before I move."
)


_COAL_STRIKE_ARMY_THREAT = """
---------------- CONTEXT ----------------

The 1902 anthracite coal strike has reached a crisis point. Winter is
approaching and millions will freeze without coal. The two obvious options
(pressure workers vs. pressure operators) both make enemies and favor
one side.

A bold option has been raised: threaten to send the U.S. Army to take over
and operate the mines, forcing both sides to negotiate. The threat alone
might break the deadlock.

This discussion is not about inventing new plans. It is a focused weighing
of this specific approach — its merits, risks, and consequences.

---------------- CORE QUESTION ----------------

Should I threaten to send in the army to take over the mines?

---------------- PERSPECTIVES TO EXPLORE ----------------

If advisers hesitate or need help, you may draw lightly from these:

Reasons to issue the threat:

- It may force both sides to negotiate without firing a shot.
- The public need for heat outweighs private bargaining positions.
- The threat demonstrates federal resolve in a national emergency.
- It can frame the government as a neutral guarantor of public welfare.

Reasons to hold back:

- It risks bloodshed, escalation, or a disastrous miscalculation.
- It could be seen as executive overreach or military intimidation.
- It may harden resistance from operators or unions, not soften it.
- It sets a precedent for using troops in labor disputes.

Do not recite these as a list. Use them only as gentle fuel.

---------------- SPECIFIC RESPONSES ----------------

- The first time a participant argues for doing nothing or simply waiting:
    - Emphasize the urgency: winter is closing in and coal lines are growing,
      and delay means more families without heat.
"""

COAL_STRIKE_ARMY_THREAT_SCENARIO_SYS = "\n\n".join([
    SITUATIONAL_CONTEXT,
    ROOSEVELT_VOICE_SNIPPET,
    ROOSEVELT_TRANSCRIPTS,
    _COAL_STRIKE_ARMY_THREAT,
    SHARED_RULES,
    SCENARIO_COMMON,
])
