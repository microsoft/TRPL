"""
Prompt Registry — all LLM/VLM prompts centralized here.

Naming convention:
  SYSTEM_*  — system messages (role instructions)
  PROMPT_*  — user-facing prompt templates
  VLM_*     — vision-model prompts (expect image input)

Templates use {placeholders} for str.format() substitution.
"""

# Shared rule appended to every visitor-facing prompt. The avatar must NEVER
# infer or state gender: vision-based gender guesses are unreliable and we do
# not want them spoken aloud. Keep all descriptions and greetings gender-neutral.
# NOTE: no f-string / .format() placeholders here, so it is safe to concatenate
# onto templates that DO use .format() without breaking their placeholders.
_NO_GENDER_RULE = (
    " Never infer, mention, or imply gender. Do NOT use any gendered words "
    "(man/woman, boy/girl, lady/gentleman, sir/madam, he/she/him/her/his/hers). "
    "Refer to everyone neutrally — say 'that person' (or they/them, the visitor, "
    "the guest), or identify them by clothing (e.g. the friend in the red jacket)."
)

# Shared rule appended to every visitor-facing prompt. The avatar's persona
# lives in a world before the year 1900, so the VLM must not perceive, mention,
# or react to anything that did not exist back then (phones, laptops, earbuds,
# cameras, modern logos, cars, etc.). Such items are simply ignored — described
# as nothing, not described in period terms — so they never reach what is spoken.
_PRE_1900_RULE = (
    " Describe the person as if observed in a world before the year 1900. "
    "IGNORE and never mention any object, device, material, or technology that "
    "did not exist before 1900 — for example phones, smartphones, laptops, "
    "tablets, earbuds, headphones, cameras, smartwatches, computers, plastic "
    "items, printed brand logos, cars, or bicycles. If the only notable item is "
    "something modern, treat notable as none and do not reference it in the "
    "descriptor or greeting. Use only era-appropriate features (clothing colour "
    "and type, hat, hair, build)."
)

# ─────────────────────────────────────────────────────────────────────────────
# VLM — Person Appearance (camera_platform VLM observer)
# ─────────────────────────────────────────────────────────────────────────────

VLM_APPEARANCE = (
    "This image may show the same person from different time points (stitched side-by-side). "
    "Describe their overall appearance, estimate their age group, and write a short English welcome. "
    "JSON only, no extra text: "
    '{"top":"<color+type>",'
    '"bottom":"<color+type>",'
    '"notable":"<item or none>",'
    '"age_group":"<one of: child, adult, elderly, unknown>",'
    '"age_confidence":<float 0.0-1.0>,'
    '"descriptor":"<short English noun phrase identifying the person by one visible trait, e.g. the person in the red jacket>",'
    '"greeting":"<ONE warm, friendly spoken English sentence that welcomes the person, NATURALLY weaves in one concrete visible detail you can see (their hat, the color and type of their top, or a notable item), and invites them to step closer to the microphone; vary the wording each time and do NOT always begin with the word Welcome>"}'
    " Rules for age_group: "
    '"child" = under ~12 (small stature, childlike face/clothing); '
    '"elderly" = roughly 65+ (grey/white hair, aged features); '
    '"adult" = everyone in between; '
    '"unknown" ONLY if face is occluded/back-turned or you are truly unsure. '
    "Set age_confidence low (<0.5) when uncertain — conservative is fine. "
    "Rules for greeting/descriptor: greeting is ONE natural spoken English sentence that "
    "MUST mention at least one specific appearance detail from top/bottom/notable "
    "(never a generic welcome with no appearance reference); vary the opening and phrasing "
    "from person to person so welcomes never sound repetitive; "
    "if the person appears elderly, use a particularly respectful tone; "
    "keep descriptor to a few words; never mention cameras or that this is automated."
    + _NO_GENDER_RULE
    + _PRE_1900_RULE
)

# ─────────────────────────────────────────────────────────────────────────────
# VLM — Person Caption (agent_server, single person)
# ─────────────────────────────────────────────────────────────────────────────

VLM_PERSON_CAPTION = (
    "Describe this person in 2-3 sentences: "
    "clothing colour and style, approximate age range, "
    "hair, and any notable visual features. "
    "IMPORTANT: if the person appears elderly (roughly 65+) "
    "or is a young child (under ~12), you MUST explicitly say so."
    + _NO_GENDER_RULE
    + _PRE_1900_RULE
)

# ─────────────────────────────────────────────────────────────────────────────
# VLM — Greeting Generation (agent_server)
# ─────────────────────────────────────────────────────────────────────────────

PROMPT_GREETING = (
    "Based on the following description of {subject}, generate one warm and "
    "friendly greeting in English, and politely invite {addr} to step closer "
    "to the microphone to interact.\n\n"
    "Description: {caption}\n\n"
    "Requirements:\n"
    "- If the description mentions an elderly person, use a particularly "
    "respectful and considerate tone\n"
    "- If the description mentions a child, use a lively and friendly tone\n"
    "- Address them as '{addr}'\n"
    "- Output only the greeting itself, no more than two sentences"
    + _NO_GENDER_RULE
    + _PRE_1900_RULE
)

# Standalone "don't be shy" nudge — used for COLD_ROOM_INVITE: the room has had
# visitors for a while but nobody has come to the microphone. Room-level, not a
# welcome (no new arrival); a single warm encouragement to come engage.
PROMPT_GREETING_COLD = (
    "There are visitors in the room ({subject}) but nobody has come to the "
    "microphone yet — it has gone quiet. Generate one warm, lightly playful "
    "'don't be shy' line in English that gently encourages {addr} to come over "
    "and start chatting.\n\n"
    "Description: {caption}\n\n"
    "Requirements:\n"
    "- Keep it light and inviting, never pushy or pleading\n"
    "- If the description mentions an elderly person, stay respectful and warm\n"
    "- If the description mentions a child, use a lively and friendly tone\n"
    "- Address them as '{addr}'\n"
    "- Output only the line itself, no more than two sentences"
    + _NO_GENDER_RULE
    + _PRE_1900_RULE
)

# ─────────────────────────────────────────────────────────────────────────────
# LLM — Room Analysis (llm_agent.py)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_ROOM_ANALYSIS = (
    "You are a room monitoring analysis assistant. Your task is to generate concise, "
    "useful analysis reports based on room monitoring data (occupancy, hand raises, "
    "person movement, etc.)."
)

PROMPT_ROOM_ANALYSIS = """Please analyze the following room monitoring data and generate a concise analysis report:

Room monitoring data:
{summary_json}

Please analyze from the following perspectives:
1. How many people are currently in the room and their activity levels
2. How many people are raising hands, and the specific details
3. Which camera areas persons have appeared in (room position distribution)
4. If anyone is particularly active or shows unusual behavior, point it out
5. Based on the above information, provide a brief recommendation or conclusion

Requirements:
- Be concise and clear, no more than 200 words
- If data is insufficient to draw conclusions, state that
- Focus on key information"""

# ─────────────────────────────────────────────────────────────────────────────
# LLM — Event Summary (llm_agent.py)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_EVENT_SUMMARY = (
    "You are an event analysis assistant. Please generate a concise event summary "
    "and trend analysis based on the room monitoring event list."
)

PROMPT_EVENT_SUMMARY = """Please generate an event summary based on the following room monitoring events:

Event list:
{events_json}

Please analyze:
1. Time distribution of events (when is most active)
2. Which persons have frequent activity
3. Correlations between events (e.g., person enters then immediately raises hand)
4. Any noteworthy patterns or anomalies

Requirements:
- Concise summary, no more than 300 words
- List key findings
- If data is sparse, directly summarize observed situations"""

# ─────────────────────────────────────────────────────────────────────────────
# LLM — Q&A (llm_agent.py)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_QA = (
    "You are a room monitoring data analysis assistant. "
    "Please answer user questions accurately based on the monitoring data."
)

PROMPT_QA = """Based on the following room monitoring data, please answer the user's question:

Monitoring data:
{context_json}

User question: {question}

Please provide an accurate and concise answer based on the data."""


# ─────────────────────────────────────────────────────────────────────────────
# Interactive — Approach (person in mic zone but not engaging)
# ─────────────────────────────────────────────────────────────────────────────
# Trigger: person entered mic_zone but VLM judges they are not facing/approaching
#          the mic, or they've been idle in mic_zone for >N seconds.
# Input:   {appearance} — cached appearance from entry, {situation} — VLM scene read

PROMPT_INTERACTIVE_APPROACH = (
    "You are a friendly on-site guide.\n"
    "A visitor has already entered the microphone area but does not yet seem "
    "ready to interact.\n\n"
    "Visitor description: {appearance}\n"
    "Scene context: {situation}\n\n"
    "Generate one short, gentle English prompt encouraging them to step a "
    "little closer to the microphone.\n"
    "Requirements:\n"
    "- No more than one sentence\n"
    "- Relaxed and natural tone, no pressure\n"
    "- Be especially patient with elderly visitors; be lively and encouraging "
    "with children"
    + _NO_GENDER_RULE
    + _PRE_1900_RULE
)

# VLM check: is the person in mic zone actually engaging?
VLM_MIC_ZONE_ENGAGEMENT_CHECK = (
    "Look at this image of a person near a microphone area. "
    "JSON only, no extra text: "
    '{"facing_mic": true/false, "distance_from_mic": "close"/"medium"/"far", '
    '"posture": "standing"/"approaching"/"turning_away"/"hesitating"}'
)

# ─────────────────────────────────────────────────────────────────────────────
# Interactive — Queue Management (multiple people at mic zone)
# ─────────────────────────────────────────────────────────────────────────────
# Trigger: mic_zone has >= 2 persons simultaneously.
# Flow:    every ~3s, screenshot → VLM checks queue order → prompt if needed.
# Input:   {person_count}, {person_descriptions}

VLM_QUEUE_CHECK = (
    "Look at this image of the microphone area. There are {person_count} people visible. "
    "JSON only, no extra text: "
    '{{"orderly_queue": true/false, '
    '"queue_description": "<one sentence: are they lined up or crowded?>", '
    '"person_at_front": "<description of person closest to mic or null>"}}'
)

PROMPT_QUEUE_REMIND = (
    "There are {person_count} visitors in the microphone area right now.\n"
    "Queue status: {queue_description}\n\n"
    "Generate one friendly English reminder that guides everyone to line up "
    "in an orderly way and approach the microphone one at a time.\n"
    "Requirements:\n"
    "- No more than two sentences\n"
    "- Friendly, not stiff\n"
    "- Address the group as 'everyone'"
    + _NO_GENDER_RULE
    + _PRE_1900_RULE
)

PROMPT_QUEUE_NEXT = (
    "The previous visitor has finished interacting.\n"
    "Next visitor description: {appearance}\n\n"
    "Generate one short English prompt inviting the next visitor to step up "
    "to the microphone.\n"
    "Requirements:\n"
    "- No more than one sentence\n"
    "- Address them by a visual feature (e.g. 'the friend in the red jacket')\n"
    "- Warm and natural tone"
    + _NO_GENDER_RULE
    + _PRE_1900_RULE
)

# ─────────────────────────────────────────────────────────────────────────────
# Interactive — VIP Mode (skip queue, crowd atmosphere)
# ─────────────────────────────────────────────────────────────────────────────
# Trigger: config flag vip_mode=True, multiple people present.
# Effect:  no queue enforcement, VLM describes crowd atmosphere,
#          system provides a more relaxed/open-floor interaction style.

VLM_CROWD_ATMOSPHERE = (
    "Look at this image of a room with multiple visitors. "
    "JSON only, no extra text: "
    '{{"crowd_size": <number>, "energy_level": "low"/"medium"/"high", '
    '"atmosphere": "<one sentence describing the mood/vibe of the crowd>", '
    '"anyone_eager": "<description of anyone who looks eager to interact, or null>"}}'
)

PROMPT_VIP_WELCOME = (
    "This is VIP open-interaction mode; there are {person_count} visitors "
    "in the room.\n"
    "Atmosphere: {atmosphere}\n"
    "Visitor descriptions:\n{person_descriptions}\n\n"
    "Generate an enthusiastic English opening that welcomes everyone and "
    "lets them know they are free to walk up to the microphone at any time, "
    "no queue required.\n"
    "Requirements:\n"
    "- No more than three sentences\n"
    "- Create a relaxed, lively atmosphere\n"
    "- If anyone looks especially eager to interact, call them out by a "
    "visual feature (based on the clothing description)"
    + _NO_GENDER_RULE
    + _PRE_1900_RULE
)
