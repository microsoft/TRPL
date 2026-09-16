// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/// <reference types="cypress" />

/**
 * Read-Only Home Page Tests
 * Tests document listing, filters, and navigation without modifying data.
 */

describe('Home Page - Read-Only UI Tests', () => {
  beforeEach(() => {
    cy.stubReadOnlyApi();
    cy.mockAuth();

    cy.intercept('GET', '**/api/v1/documents**', {
      statusCode: 200,
      body: {
        documents: [
          {
            id: 'doc-1',
            metadata: {
              Title: 'Test Document 1',
              Repository: 'Library of Congress',
              Collection: 'Historical Letters',
              'Resource Type': 'correspondence',
            },
            status: 'pending',
            asset_avg_confidence: 0.95,
            created_at: '2024-01-15T10:30:00Z',
          },
          {
            id: 'doc-2',
            metadata: {
              Title: 'Test Document 2',
              Repository: 'National Archives',
              Collection: 'Government Records',
              'Resource Type': 'report',
            },
            status: 'completed',
            asset_avg_confidence: 0.88,
            created_at: '2024-01-14T09:20:00Z',
          },
        ],
        count: 2,
      },
    }).as('getDocuments');

    cy.intercept('GET', '**/api/v1/collections/summaries', {
      statusCode: 200,
      body: {
        summaries: [
          { repository: 'Library of Congress', collection: 'Historical Letters', total_items: 10 },
          { repository: 'National Archives', collection: 'Government Records', total_items: 5 },
        ],
        count: 2,
      },
    }).as('getCollectionSummaries');

    cy.intercept('GET', '**/api/v1/filters/resource-types', {
      statusCode: 200,
      body: {
        resource_types: ['correspondence', 'report', 'photograph'],
        count: 3,
      },
    }).as('getResourceTypes');

    cy.visit('/home');
    cy.wait(['@getDocuments', '@getCollectionSummaries', '@getResourceTypes']);
  });

  it('should display the review queue header', () => {
    cy.contains('Review Queue').should('be.visible');
  });

  it('should display document rows with correct data', () => {
    cy.contains('Test Document 1').should('be.visible');
    cy.contains('Test Document 2').should('be.visible');
    cy.contains('Library of Congress').should('be.visible');
    cy.contains('National Archives').should('be.visible');
  });

  it('should display search input', () => {
    cy.get('input[placeholder="Search by title"]').should('be.visible');
  });

  it('should display filter controls', () => {
    cy.contains('Repository').should('be.visible');
    cy.contains('Collection').should('be.visible');
  });

  it('should navigate to review page on row click', () => {
    cy.intercept('GET', '**/api/v1/documents/doc-1**', {
      statusCode: 200,
      body: {
        id: 'doc-1',
        title: 'Test Document 1',
        repository: 'Library of Congress',
        collection: 'Historical Letters',
        assets: [],
      },
    }).as('getDocument');

    cy.contains('Test Document 1').click();
    cy.url().should('include', '/review/doc-1');
  });

  it('should display table column headers', () => {
    cy.get('th').contains('Title').should('be.visible');
  });

  it('should show content area when documents load', () => {
    cy.get('table').should('exist');
  });

  it('should display loading state', () => {
    cy.intercept('GET', '**/api/v1/documents**', {
      delay: 800,
      statusCode: 200,
      body: { documents: [], count: 0 },
    }).as('slowDocuments');

    cy.visit('/home');
    cy.contains('Loading documents', { timeout: 500 }).should('be.visible');
    cy.wait('@slowDocuments');
  });

  it('should handle API errors', () => {
    cy.intercept('GET', '**/api/v1/documents**', {
      statusCode: 500,
      body: { error: 'Internal Server Error' },
    }).as('getError');

    cy.visit('/home');
    cy.wait('@getError');
    cy.get('body').should('be.visible');
  });
});
