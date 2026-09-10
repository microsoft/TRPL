/**
 * Test setup for Bun's native test runner
 *
 * This project uses Jest for testing due to dependencies on:
 * - jest.useFakeTimers() / jest.advanceTimersByTime()
 * - jest.spyOn() on window/global objects
 * - Complex module mocking patterns
 *
 * Please use: bun run test
 * Not: bun test
 */

console.error(`
╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║   Please use "bun run test" instead of "bun test"              ║
║                                                                ║
║   This project uses Jest, which has features not yet           ║
║   fully supported by Bun's native test runner:                 ║
║   - Fake timers (jest.useFakeTimers)                           ║
║   - Spying on globals (jest.spyOn(window, ...))                ║
║                                                                ║
║   Run tests with:  bun run test                                ║
║   Watch mode:      bun run test:watch                          ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
`)

process.exit(1)
