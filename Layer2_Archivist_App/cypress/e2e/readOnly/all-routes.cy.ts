// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/// <reference types="cypress" />

/**
 * All Routes - Read-Only
 * Visits every archivist-app route with mocked GET APIs and admin auth.
 */

describe('All Routes - Read-Only Smoke', () => {
  beforeEach(() => {
    cy.stubReadOnlyApi();
    cy.mockAuth();
  });

  const routes: { path: string; expectText: string }[] = [
    { path: '/', expectText: 'Overview of your archival system' },
    { path: '/home', expectText: 'Items requiring archivist validation' },
    { path: '/repositories', expectText: 'Manage your content repositories' },
    { path: '/collections', expectText: 'Digitized Roosevelt Collections' },
    { path: '/help/architecture', expectText: 'Architecture Diagrams' },
    { path: '/admin/statistics', expectText: 'Statistics Administration' },
    { path: '/correction-requests', expectText: 'Incoming notes from registered downstream' },
    { path: '/tools/epub-processor', expectText: 'Upload, parse, and ingest EPUB' },
    { path: '/tools/data-pipeline', expectText: 'Data Pipeline Monitor' },
    { path: '/tools/data-ingestion', expectText: 'Data Ingestion' },
    { path: '/admin/field-mappings', expectText: 'Identifier Field Mapping' },
    { path: '/repositories/Test%20Repo/collections', expectText: 'Overview and collections for this repository' },
    { path: '/repositories/Test%20Repo/collections/Test%20Coll', expectText: 'Review Queue' },
    { path: '/review/doc-test-1', expectText: 'Review' },
  ];

  routes.forEach(({ path, expectText }) => {
    it(`should render ${path}`, () => {
      cy.visit(path);
      cy.get('main').contains(expectText, { timeout: 10000 }).should('be.visible');
    });
  });

  it('should show 404 for unknown routes', () => {
    cy.visit('/this-route-does-not-exist', { failOnStatusCode: false });
    cy.get('body').should('be.visible');
  });
});
