// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/// <reference types="cypress" />

/**
 * Read-Only Dashboard Tests
 * Tests UI rendering and navigation without modifying data.
 */

describe('Dashboard - Read-Only UI Tests', () => {
  beforeEach(() => {
    cy.stubReadOnlyApi();
    cy.mockAuth();

    cy.intercept('GET', '**/api/v1/dashboard/statistics', {
      statusCode: 200,
      body: {
        totalItems: 1000,
        totalCollections: 50,
        totalPending: 200,
        totalPublishing: 50,
        totalPublished: 600,
        totalRepositories: 5,
        totalReviewed: 100,
        totalErrors: 10,
        overallCompletionRate: 0.75,
      },
    }).as('getDashboardStats');

    cy.intercept('GET', '**/api/v1/repositories**', {
      statusCode: 200,
      body: {
        repositories: [
          {
            repository: 'Library of Congress',
            totalItems: 500,
            collections: 25,
            pending: 100,
            reviewed: 50,
            publishing: 25,
            published: 300,
            errors: 5,
            completionRate: 0.75,
          },
          {
            repository: 'National Archives',
            totalItems: 300,
            collections: 15,
            pending: 60,
            reviewed: 30,
            publishing: 15,
            published: 180,
            errors: 3,
            completionRate: 0.75,
          },
        ],
        count: 2,
      },
    }).as('getRepositories');

    cy.visit('/');
    cy.wait(['@getDashboardStats', '@getRepositories']);
  });

  it('should display dashboard page header', () => {
    cy.contains('Overview of your archival system').should('be.visible');
  });

  it('should display dashboard statistics labels', () => {
    cy.contains('Total Items').should('be.visible');
    cy.contains('Pending').should('be.visible');
    cy.contains('Published').should('be.visible');
  });

  it('should display correct statistics values', () => {
    cy.contains('1,000').should('be.visible');
  });

  it('should display repository statistics', () => {
    cy.contains('Library of Congress').should('be.visible');
    cy.contains('National Archives').should('be.visible');
  });

  it('should navigate to repository detail on card click', () => {
    cy.contains('Library of Congress').click();
    cy.url().should('include', '/repositories/');
  });

  it('should display loading state during data fetch', () => {
    cy.intercept('GET', '**/api/v1/dashboard/statistics', {
      delay: 800,
      statusCode: 200,
      body: { totalItems: 1000, totalCollections: 50, totalRepositories: 5, totalPending: 0, totalPublishing: 0, totalPublished: 0, totalReviewed: 0, totalErrors: 0, overallCompletionRate: 0 },
    }).as('slowStats');

    cy.visit('/');
    cy.contains('Loading', { timeout: 500 }).should('be.visible');
    cy.wait('@slowStats');
  });

  it('should handle API errors gracefully', () => {
    cy.intercept('GET', '**/api/v1/dashboard/statistics', {
      statusCode: 500,
      body: { error: 'Internal Server Error' },
    }).as('getError');

    cy.intercept('GET', '**/api/v1/repositories**', {
      statusCode: 500,
      body: { error: 'Internal Server Error' },
    });

    cy.visit('/');
    cy.wait('@getError');
    cy.contains('Could not connect', { matchCase: false }).should('be.visible');
  });

  it('should be responsive on different screen sizes', () => {
    cy.viewport(375, 667);
    cy.get('body').should('be.visible');

    cy.viewport(768, 1024);
    cy.get('body').should('be.visible');

    cy.viewport(1920, 1080);
    cy.get('body').should('be.visible');
  });
});
