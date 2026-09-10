"""
Simple test script for the Archivist API.
Run this script to verify the API is working correctly.

Usage:
    cd apps/archivist-api
    python tests/test_api.py
"""

import sys

import requests


def print_response(endpoint: str, response: requests.Response) -> None:
    """Print formatted response information."""
    separator = '=' * 80
    print(f"\n{separator}")
    print(f"Endpoint: {endpoint}")
    print(f"Status Code: {response.status_code}")
    print("Response:")
    try:
        print(response.json())
    except Exception:
        print(response.text)
    print(separator)


def test_health(base_url: str) -> bool:
    """Test the health endpoint."""
    print("\n[TEST 1] Testing Health Endpoint...")
    try:
        response = requests.get(f"{base_url}/api/v1/health", timeout=5)
        print_response("/api/v1/health", response)
        return response.status_code == 200
    except requests.RequestException as error:
        print(f"❌ Error: {str(error)}")
        return False


def test_get_all_documents(base_url: str) -> bool:
    """Test getting all documents."""
    print("\n[TEST 2] Testing Get All Documents...")
    try:
        response = requests.get(f"{base_url}/api/v1/documents?max_items=5", timeout=10)
        print_response("/api/v1/documents", response)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Retrieved {data['count']} documents")
            return True

        print(f"❌ Failed with status code: {response.status_code}")
        return False
    except requests.RequestException as error:
        print(f"❌ Error: {str(error)}")
        return False

def test_root_endpoint(base_url: str) -> bool:
    """Test the root endpoint."""
    print("\n[TEST 0] Testing Root Endpoint...")
    try:
        response = requests.get(base_url, timeout=5)
        print_response("/", response)
        return response.status_code == 200
    except requests.RequestException as error:
        print(f"❌ Error: {str(error)}")
        return False


def main():
    """Run all tests."""
    base_url = "http://127.0.0.1:8000"
    print("=" * 80)
    print("ARCHIVIST API TEST SUITE")
    print("=" * 80)
    print(f"Testing API at: {base_url}")
    print("Make sure the API server is running before running this test.")
    print()
    input("Press Enter to continue...")

    # Run tests
    results = []

    results.append(("Root Endpoint", test_root_endpoint(base_url)))
    results.append(("Health Check", test_health(base_url)))
    results.append(("Get All Documents", test_get_all_documents(base_url)))
    # Print summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    total_tests = len(results)
    passed_tests = sum(1 for _, result in results if result)

    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{test_name:.<50} {status}")

    print("=" * 80)
    print(f"Total: {passed_tests}/{total_tests} tests passed")

    if passed_tests == total_tests:
        print("🎉 All tests passed!")
        sys.exit(0)
    else:
        print("⚠️  Some tests failed. Check the output above for details.")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Tests interrupted by user.")
        sys.exit(1)
