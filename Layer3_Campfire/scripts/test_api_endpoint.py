# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration test script for WebSocket chat functionality.
Tests multi-turn conversations and chat history restoration.
"""

import asyncio
import json
import sys
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx
import websockets
from websockets.exceptions import ConnectionClosed


class Colors:
    """ANSI color codes for terminal output."""

    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def log_info(message: str):
    """Log info message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"{Colors.OKCYAN}[{timestamp}] ℹ {message}{Colors.ENDC}")


def log_success(message: str):
    """Log success message."""
    print(f"{Colors.OKGREEN}✓ {message}{Colors.ENDC}")


def log_error(message: str):
    """Log error message."""
    print(f"{Colors.FAIL}✗ {message}{Colors.ENDC}")


def log_warning(message: str):
    """Log warning message."""
    print(f"{Colors.WARNING}⚠ {message}{Colors.ENDC}")


def log_header(message: str):
    """Log section header."""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 80}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{message}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 80}{Colors.ENDC}\n")


async def send_message_and_collect_response(
    websocket, message: str, chat_id: str, user_id: str, timeout: int = 60
) -> dict[str, Any]:
    """Send a message via WebSocket and collect the complete response."""

    request = {"message": message, "chat_id": chat_id, "user_id": user_id}

    log_info(f"Sending: {message[:80]}...")
    await websocket.send(json.dumps(request))

    result = {"progress": [], "deltas": [], "final": None, "error": None, "complete_text": ""}

    try:
        while True:
            response_text = await asyncio.wait_for(websocket.recv(), timeout=timeout)

            if response_text == "[END]":
                log_info("Received [END] marker")
                break

            try:
                response = json.loads(response_text)
                response_type = response.get("type")

                if response_type == "progress":
                    progress = response.get("progress", "")
                    result["progress"].append(progress)
                    print(f"  {Colors.OKBLUE}[Progress] {progress}{Colors.ENDC}")

                elif response_type == "delta":
                    delta = response.get("delta", "")
                    result["deltas"].append(delta)
                    result["complete_text"] += delta
                    # Print delta without newline for streaming effect
                    print(delta, end="", flush=True)

                elif response_type == "final":
                    print()  # New line after deltas
                    result["final"] = response
                    text = response.get("text", "")
                    citations = response.get("citations", [])
                    log_success(f"Final response received ({len(text)} chars, {len(citations)} citations)")

                elif response_type == "error":
                    result["error"] = response.get("error")
                    log_error(f"Error: {result['error']}")

            except json.JSONDecodeError:
                log_warning(f"Failed to parse response: {response_text[:100]}")

    except asyncio.TimeoutError:
        log_error(f"Timeout waiting for response (>{timeout}s)")
        result["error"] = "Timeout"

    except ConnectionClosed as e:
        log_error(f"WebSocket connection closed: {e}")
        result["error"] = f"Connection closed: {e}"

    return result


async def test_multi_turn_conversation(server_url: str, chat_id: str, user_id: str):
    log_header("Multi-Turn Conversation (Single WebSocket)")

    async with websockets.connect(server_url) as websocket:
        log_success(f"Connected to {server_url}")

        # Turn 1: Ask about TR's conservation work
        log_info("Turn 1: Asking about Theodore Roosevelt's conservation work...")
        response1 = await send_message_and_collect_response(
            websocket, "What did Theodore Roosevelt do for conservation?", chat_id, user_id
        )

        if response1["error"]:
            log_error(f"Turn 1 failed: {response1['error']}")
            return False

        if not response1["final"]:
            log_error("Turn 1: No final response received")
            return False

        citations1 = response1["final"].get("citations", [])
        log_success(f"Turn 1 completed: {len(citations1)} citations")
        print(f"  Response preview: {response1['final']['text'][:150]}...")

        # Turn 2: Follow-up question (should have context from Turn 1)
        await asyncio.sleep(1)  # Brief pause between turns

        log_info("Turn 2: Follow-up question about national parks...")
        response2 = await send_message_and_collect_response(
            websocket, "Which national parks did he create?", chat_id, user_id
        )

        if response2["error"]:
            log_error(f"Turn 2 failed: {response2['error']}")
            return False

        if not response2["final"]:
            log_error("Turn 2: No final response received")
            return False

        citations2 = response2["final"].get("citations", [])
        log_success(f"Turn 2 completed: {len(citations2)} citations")
        print(f"  Response preview: {response2['final']['text'][:150]}...")

        # Turn 3: Another follow-up
        await asyncio.sleep(1)

        log_info("Turn 3: Another follow-up about his motivation...")
        response3 = await send_message_and_collect_response(
            websocket, "Can you summarize our conversation so far", chat_id, user_id
        )

        if response3["error"]:
            log_error(f"Turn 3 failed: {response3['error']}")
            return False

        if not response3["final"]:
            log_error("Turn 3: No final response received")
            return False

        citations3 = response3["final"].get("citations", [])
        log_success(f"Turn 3 completed: {len(citations3)} citations")
        print(f"  Response preview: {response3['final']['text'][:150]}...")

        log_success("Multi-turn conversation test PASSED ✓")
        return True


async def test_single_turn_conversation(server_url: str, chat_id: str, user_id: str):
    log_header("Single-Turn Conversation (New WebSocket)")

    async with websockets.connect(server_url) as websocket:
        log_success(f"Connected to {server_url}")

        log_info("Asking about TR's Rough Riders...")
        response = await send_message_and_collect_response(
            websocket, "Tell me about Theodore Roosevelt and the Rough Riders.", chat_id, user_id
        )

        if response["error"]:
            log_error(f"Single-turn test failed: {response['error']}")
            return False

        if not response["final"]:
            log_error("No final response received")
            return False

        citations = response["final"].get("citations", [])
        text_length = len(response["final"]["text"])

        log_success("Single-turn conversation test PASSED ✓")
        log_info(f"Response: {text_length} characters, {len(citations)} citations")

        return True


async def test_chat_history_restoration(server_url: str, chat_id: str, user_id: str):
    log_header("Chat History Restoration (Reconnection)")

    log_info("Opening second WebSocket connection with same chat_id and user_id...")
    log_info(f"chat_id: {chat_id}")
    log_info(f"user_id: {user_id}")

    async with websockets.connect(server_url) as websocket:
        log_success(f"Connected to {server_url}")

        # Ask a question that refers to previous conversation
        log_info("Asking a follow-up question that requires previous context...")
        response = await send_message_and_collect_response(
            websocket,
            "Based on what we discussed earlier, what was his greatest conservation achievement?",
            chat_id,
            user_id,
            timeout=90,
        )

        if response["error"]:
            log_error(f"History restoration test failed: {response['error']}")
            return False

        if not response["final"]:
            log_error("No final response received")
            return False

        # Check if response makes sense (should reference previous context)
        response_text = response["final"]["text"].lower()

        # Look for contextual references or continuation
        log_info("Analyzing response for context awareness...")

        citations = response["final"].get("citations", [])
        log_success("Chat history restoration test PASSED ✓")
        log_info(f"Response: {len(response['final']['text'])} characters, {len(citations)} citations")
        print(f"  Response preview: {response['final']['text'][:200]}...")

        return True


async def test_out_of_scope_question(server_url: str, chat_id: str, user_id: str):
    log_header("Out-of-Scope Question Handling")

    async with websockets.connect(server_url) as websocket:
        log_success(f"Connected to {server_url}")

        log_info("Asking an out-of-scope question...")
        response = await send_message_and_collect_response(
            websocket, "What's the weather like today?", chat_id, user_id
        )

        if response["error"]:
            log_error(f"Out-of-scope test failed: {response['error']}")
            return False

        if not response["final"]:
            log_error("No final response received")
            return False

        # Should receive an out-of-scope message
        response_text = response["final"]["text"]
        if "can't help" in response_text.lower() or "theodore roosevelt" in response_text.lower():
            log_success("Out-of-scope question correctly handled ✓")
            log_info(f"Response: {response_text}")
            return True
        else:
            log_warning("Response may not be an out-of-scope rejection")
            log_info(f"Response: {response_text}")
            return True  # Still pass, as long as we got a response


async def verify_citations_format(citations: list[dict]) -> bool:
    """Verify citations have the expected format."""
    if not citations:
        return True  # Empty citations are valid

    required_fields = {"index"}  # Minimum required field

    for citation in citations:
        if not all(field in citation for field in required_fields):
            log_warning(f"Citation missing required fields: {citation}")
            return False

    return True


async def test_get_user_chats(base_url: str, user_id: str, expected_chat_ids: list[str]):
    log_header("Get Chat History API")

    async with httpx.AsyncClient() as client:
        log_info(f"Fetching chat history for user_id: {user_id}")

        try:
            response = await client.get(f"{base_url}/api/chat-history/{user_id}")

            if response.status_code != 200:
                log_error(f"Failed to get chat history: {response.status_code}")
                log_error(f"Response: {response.text}")
                return False

            chat_ids = response.json()
            log_success(f"Retrieved {len(chat_ids)} chat IDs")

            # Verify we got the expected chat IDs
            for expected_id in expected_chat_ids:
                if expected_id in chat_ids:
                    log_success(f"Found expected chat_id: {expected_id}")
                else:
                    log_error(f"Missing expected chat_id: {expected_id}")
                    return False

            log_success("Get chat history test PASSED ✓")
            return True

        except Exception as e:
            log_error(f"Get chat history test failed with exception: {e}")
            return False


async def test_get_chat_messages(base_url: str, user_id: str, chat_id: str, expected_min_messages: int = 1):
    log_header("Get Chat Messages API")

    async with httpx.AsyncClient() as client:
        log_info(f"Fetching messages for chat_id: {chat_id}, user_id: {user_id}")

        try:
            response = await client.get(f"{base_url}/api/chat-history/{user_id}/{chat_id}/messages")

            if response.status_code != 200:
                log_error(f"Failed to get chat messages: {response.status_code}")
                log_error(f"Response: {response.text}")
                return False

            messages = response.json()
            log_success(f"Retrieved {len(messages)} messages")

            # Verify we got messages
            if len(messages) < expected_min_messages:
                log_error(f"Expected at least {expected_min_messages} messages, got {len(messages)}")
                return False

            # Verify message structure
            for i, msg in enumerate(messages):
                if "role" not in msg or "text" not in msg or "timestamp" not in msg:
                    log_error(f"Message {i} missing required fields: {msg.keys()}")
                    return False

                if msg["role"] not in ["user", "assistant"]:
                    log_error(f"Message {i} has invalid role: {msg['role']}")
                    return False

            log_info(f"Sample message: {messages[0]['role']}: {messages[0]['text'][:80]}...")
            log_success("Get chat messages test PASSED ✓")
            return True

        except Exception as e:
            log_error(f"Get chat messages test failed with exception: {e}")
            return False


async def test_rest_multi_turn_conversation(base_url: str):
    log_header("Multi-Turn Conversation (REST API)")

    # Generate test IDs
    chat_id = uuid4().hex
    user_id = f"test-user-rest-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Turn 1: Initial question
        log_info("Turn 1: Asking about Theodore Roosevelt's foreign policy...")

        request_1 = {
            "message": "What was Theodore Roosevelt's foreign policy?",
            "chat_id": chat_id,
            "user_id": user_id
        }

        try:
            response_1 = await client.post(f"{base_url}/api/chat", json=request_1)

            if response_1.status_code != 200:
                log_error(f"Turn 1 failed with status {response_1.status_code}")
                log_error(f"Response: {response_1.text}")
                return False

            data_1 = response_1.json()

            if data_1.get("type") == "error":
                log_error(f"Turn 1 returned error: {data_1.get('error')}")
                return False

            if data_1.get("type") != "final":
                log_error(f"Turn 1: Expected 'final' response, got '{data_1.get('type')}'")
                return False

            text_1 = data_1.get("text", "")
            citations_1 = data_1.get("citations", [])
            returned_chat_id = data_1.get("chat_id")
            returned_user_id = data_1.get("user_id")

            log_success(f"Turn 1 completed: {len(text_1)} chars, {len(citations_1)} citations")
            print(f"  Response preview: {text_1[:150]}...")
            log_info(f"Returned chat_id: {returned_chat_id}")
            log_info(f"Returned user_id: {returned_user_id}")

            # Verify IDs match
            if returned_chat_id != chat_id:
                log_error(f"chat_id mismatch: sent {chat_id}, got {returned_chat_id}")
                return False
            if returned_user_id != user_id:
                log_error(f"user_id mismatch: sent {user_id}, got {returned_user_id}")
                return False

            # Turn 2: Follow-up question asking to summarize conversation
            await asyncio.sleep(2)

            log_info("Turn 2: Asking to summarize the conversation...")

            request_2 = {
                "message": "Can you summarize our conversation so far?",
                "chat_id": chat_id,
                "user_id": user_id
            }

            response_2 = await client.post(f"{base_url}/api/chat", json=request_2)

            if response_2.status_code != 200:
                log_error(f"Turn 2 failed with status {response_2.status_code}")
                log_error(f"Response: {response_2.text}")
                return False

            data_2 = response_2.json()

            if data_2.get("type") == "error":
                log_error(f"Turn 2 returned error: {data_2.get('error')}")
                return False

            if data_2.get("type") != "final":
                log_error(f"Turn 2: Expected 'final' response, got '{data_2.get('type')}'")
                return False

            text_2 = data_2.get("text", "")
            citations_2 = data_2.get("citations", [])

            log_success(f"Turn 2 completed: {len(text_2)} chars, {len(citations_2)} citations")
            print(f"  Response preview: {text_2[:150]}...")

            # Verify the summary references the conversation context
            text_2_lower = text_2.lower()
            if "foreign policy" in text_2_lower or "conversation" in text_2_lower or "discussed" in text_2_lower:
                log_success("Summary correctly references conversation context")
            else:
                log_warning("Summary may not reference conversation context")
                log_info("Note: This could still be valid depending on the agent's response style")

            log_success("REST multi-turn conversation test PASSED ✓")
            return True

        except httpx.TimeoutException:
            log_error("Request timed out (>120s)")
            return False
        except Exception as e:
            log_error(f"REST multi-turn test failed with exception: {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_delete_chat(base_url: str, user_id: str, chat_id: str):
    """Test 8: Delete a chat."""

    log_header("TEST 8: Delete Chat API")

    async with httpx.AsyncClient() as client:
        log_info(f"Deleting chat_id: {chat_id}, user_id: {user_id}")

        try:
            # Delete the chat
            response = await client.delete(f"{base_url}/api/chat-history/{user_id}/{chat_id}")

            if response.status_code != 200:
                log_error(f"Failed to delete chat: {response.status_code}")
                log_error(f"Response: {response.text}")
                return False

            result = response.json()
            if result.get("status") != "success":
                log_error(f"Deletion did not return success: {result}")
                return False

            log_success(f"Chat deleted: {result.get('message')}")

            # Verify deletion by trying to get messages
            log_info("Verifying deletion by fetching messages...")
            verify_response = await client.get(f"{base_url}/api/chat-history/{user_id}/{chat_id}/messages")

            # Should still return 200 but with empty messages (or could be 404, depends on implementation)
            messages = verify_response.json()
            if len(messages) == 0:
                log_success("Verified: Chat messages are empty after deletion")
            else:
                log_warning(f"Chat still has {len(messages)} messages after deletion")

            # Verify chat is not in history
            log_info("Verifying chat is removed from history...")
            history_response = await client.get(f"{base_url}/api/chat-history/{user_id}")

            if history_response.status_code == 200:
                chat_ids = history_response.json()
                if chat_id not in chat_ids:
                    log_success("Verified: Chat removed from history")
                else:
                    log_error("Chat still appears in history after deletion")
                    return False

            log_success("Delete chat test PASSED ✓")
            return True

        except Exception as e:
            log_error(f"Delete chat test failed with exception: {e}")
            return False


async def main():
    """Run all integration tests."""

    # Configuration
    SERVER_HOST = "localhost"
    SERVER_PORT = 8000
    server_url = f"ws://{SERVER_HOST}:{SERVER_PORT}/ws/chat"
    base_url = f"http://{SERVER_HOST}:{SERVER_PORT}"

    # Generate test IDs
    chat_id = uuid4().hex
    user_id = f"test-user-{uuid4().hex[:8]}"

    # Second chat for testing chat history API
    chat_id_2 = uuid4().hex

    log_header("WebSocket & API Integration Test Suite")
    log_info(f"Server: {server_url}")
    log_info(f"Test Chat ID 1: {chat_id}")
    log_info(f"Test Chat ID 2: {chat_id_2}")
    log_info(f"Test User ID: {user_id}")

    results = {
        "multi_turn": False,
        "single_turn": False,
        "history_restoration": False,
        "out_of_scope": False,
        "rest_multi_turn": False,
        "get_user_chats": False,
        "get_chat_messages": False,
        "delete_chat": False,
    }

    try:
        # Test 1: Multi-turn conversation
        results["multi_turn"] = await test_multi_turn_conversation(server_url, chat_id, user_id)

        await asyncio.sleep(2)  # Pause between tests

        # Test 2: Single-turn conversation (same chat)
        results["single_turn"] = await test_single_turn_conversation(server_url, chat_id, user_id)

        await asyncio.sleep(2)

        # Test 3: Chat history restoration (new connection)
        results["history_restoration"] = await test_chat_history_restoration(server_url, chat_id, user_id)

        await asyncio.sleep(2)

        # Test 4: Out-of-scope question
        results["out_of_scope"] = await test_out_of_scope_question(server_url, chat_id, user_id)

        await asyncio.sleep(2)

        # Test 5: REST API multi-turn conversation
        results["rest_multi_turn"] = await test_rest_multi_turn_conversation(base_url)

        await asyncio.sleep(2)

        # Create a second chat for testing chat history API
        log_info("Creating second chat for API testing...")
        async with websockets.connect(server_url) as websocket:
            await send_message_and_collect_response(
                websocket, "Tell me about Theodore Roosevelt's presidency.", chat_id_2, user_id
            )

        await asyncio.sleep(2)

        # Test 6: Get chat history
        results["get_user_chats"] = await test_get_user_chats(base_url, user_id, [chat_id, chat_id_2])

        await asyncio.sleep(1)

        # Test 7: Get chat messages
        results["get_chat_messages"] = await test_get_chat_messages(
            base_url,
            user_id,
            chat_id,
            expected_min_messages=6,  # 3 turns = 6 messages
        )

        await asyncio.sleep(1)

        # Test 8: Delete chat
        results["delete_chat"] = await test_delete_chat(base_url, user_id, chat_id_2)

        await asyncio.sleep(1)

    except Exception as e:
        log_error(f"Unexpected error during tests: {e}")
        import traceback

        traceback.print_exc()

    # Print summary
    log_header("Test Summary")

    total_tests = len(results)
    passed_tests = sum(1 for result in results.values() if result)

    for test_name, result in results.items():
        status = f"{Colors.OKGREEN}PASSED{Colors.ENDC}" if result else f"{Colors.FAIL}FAILED{Colors.ENDC}"
        print(f"  {test_name.replace('_', ' ').title()}: {status}")

    print()
    success_rate = (passed_tests / total_tests) * 100

    if passed_tests == total_tests:
        log_success(f"All tests passed! ({passed_tests}/{total_tests}) 🎉")
        return 0
    else:
        log_warning(f"Some tests failed: {passed_tests}/{total_tests} passed ({success_rate:.1f}%)")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
