// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

const nextJest = require('next/jest')

const createJestConfig = nextJest({
  // Provide the path to your Next.js app to load next.config.js and .env files
  dir: './',
})

/** @type {import('jest').Config} */
const customJestConfig = {
  // Add more setup options before each test is run
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],

  // Test environment
  testEnvironment: 'jest-environment-jsdom',
  
  // Module path aliases (match tsconfig.json)
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
  },
  
  // Test file patterns
  testMatch: [
    '**/__tests__/**/*.[jt]s?(x)',
    '**/?(*.)+(spec|test).[jt]s?(x)',
  ],
  
  // Coverage configuration
  collectCoverageFrom: [
    'src/**/*.{js,jsx,ts,tsx}',
    '!src/**/*.d.ts',
    '!src/**/index.ts',
    '!src/app/layout.tsx',
  ],
  
  // Ignore patterns
  testPathIgnorePatterns: [
    '<rootDir>/node_modules/',
    '<rootDir>/.next/',
    '<rootDir>/.venv/',
    '<rootDir>/e2e/',
  ],
  modulePathIgnorePatterns: [
    '<rootDir>/.next/',
    '<rootDir>/.venv/',
  ],
  watchPathIgnorePatterns: [
    '<rootDir>/.next/',
    '<rootDir>/.venv/',
  ],
}

// createJestConfig is exported this way to ensure that next/jest can load the Next.js config which is async.
// We wrap the resolver so we can override transformIgnorePatterns AFTER next/jest builds
// the final config — next/jest unconditionally ignores all of node_modules, which breaks
// tests that import ESM-only packages like react-markdown. Allow that subtree (and its
// ESM deps) to be transformed for tests.
const esmDepsToTransform = [
  'react-markdown',
  'vfile',
  'vfile-message',
  'unist-.*',
  'unified',
  'bail',
  'is-plain-obj',
  'trough',
  'remark-.*',
  'rehype-.*',
  'mdast-.*',
  'micromark.*',
  'decode-named-character-reference',
  'character-entities.*',
  'property-information',
  'space-separated-tokens',
  'comma-separated-tokens',
  'hast-util-.*',
  'hast-.*',
  'html-url-attributes',
  'web-namespaces',
  'zwitch',
  'longest-streak',
  'ccount',
  'devlop',
  'estree-util-is-identifier-name',
  'markdown-table',
  'trim-lines',
  'escape-string-regexp',
].join('|')

const resolveNextJestConfig = createJestConfig(customJestConfig)

module.exports = async () => {
  const config = await resolveNextJestConfig()
  config.transformIgnorePatterns = [
    `/node_modules/(?!(${esmDepsToTransform})/)`,
    '^.+\\.module\\.(css|sass|scss)$',
  ]
  return config
}
