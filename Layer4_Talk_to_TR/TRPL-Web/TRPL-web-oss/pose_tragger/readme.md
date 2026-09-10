
async def lemonslice_control_pose_trigger(session_id: str, pose_name: str) -> bool:

"""Same control endpoint and auth as ``lemonslice_control_update_image``; event ``pose-trigger``."""

api_key = os.getenv("LEMONSLICE_API_KEY")

if not api_key:

logger.error("LEMONSLICE_API_KEY is not set")

return False

pose_name = pose_name.strip()

if not pose_name:

logger.error("LemonSlice pose-trigger needs a non-empty pose name")

return False


url = lemonslice_session_control_url(session_id)

# TODO: pose_name will be specific to teddy

payload = {"event": "pose-trigger", "pose_trigger": {"name": pose_name}}

post_json = json.dumps(payload, ensure_ascii=False)

logger.info("LemonSlice pose-trigger POST JSON: %s", post_json)

async with httpx.AsyncClient() as client:

response = await client.post(

url,

headers={

"Content-Type": "application/json",

"X-API-Key": api_key,

},

content=post_json.encode("utf-8"),

timeout=30.0,

)

if response.is_success:

logger.info(

"LemonSlice control pose-trigger ok: pose_name=%r session_id=%s… status=%s",

pose_name,

session_id[:16],

response.status_code,

)

return True

logger.error(

"LemonSlice control pose-trigger failed: %s %s (pose_name=%r)",

response.status_code,

(response.text or "")[:500],

pose_name,

)

return False


# code for testing pose functions in lemonslice.
# this is how is work, first system will fetch current newest running livekit session, and send the action instruction (create it as a python file, I can just run python file) (I will give all the pose later)