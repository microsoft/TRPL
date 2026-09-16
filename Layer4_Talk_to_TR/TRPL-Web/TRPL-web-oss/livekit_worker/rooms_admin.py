# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""List or delete LiveKit rooms.

Usage:
  list              - list active rooms
  delete <name>     - delete one room
  delete-all        - delete every active room
"""
import asyncio, os, sys, dotenv
dotenv.load_dotenv()

from livekit import api


async def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    lk = api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"].replace("wss://", "https://").replace("ws://", "http://"),
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )
    try:
        if cmd == "list":
            rooms = (await lk.room.list_rooms(api.ListRoomsRequest())).rooms
            if not rooms:
                print("(no active rooms)"); return
            for r in rooms:
                print(f"{r.name}  participants={r.num_participants}  created={r.creation_time}  sid={r.sid}")
        elif cmd == "delete":
            name = sys.argv[2]
            await lk.room.delete_room(api.DeleteRoomRequest(room=name))
            print(f"deleted: {name}")
        elif cmd == "delete-all":
            rooms = (await lk.room.list_rooms(api.ListRoomsRequest())).rooms
            for r in rooms:
                await lk.room.delete_room(api.DeleteRoomRequest(room=r.name))
                print(f"deleted: {r.name}")
            print(f"total: {len(rooms)}")
        else:
            print(__doc__); sys.exit(1)
    finally:
        await lk.aclose()


asyncio.run(main())
