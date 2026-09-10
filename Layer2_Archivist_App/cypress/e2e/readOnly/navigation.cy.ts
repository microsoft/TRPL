/// <reference types="cypress" />

/**
 * Navigation Tests - Read-Only
 * Tests sidebar navigation between pages without modifying data.
 */

describe('Navigation - Read-Only Tests', () => {
  beforeEach(() => {
    cy.stubReadOnlyApi();
    cy.mockAuth();
  });

  it('should navigate to dashboard from repositories', () => {
    cy.visit('/repositories');
    cy.clickSidebar('Dashboard');
    cy.url().should('eq', Cypress.config().baseUrl + '/');
  });

  it('should navigate to repositories from dashboard', () => {
    cy.visit('/');
    cy.clickSidebar('Repositories');
    cy.url().should('include', '/repositories');
  });

  it('should navigate to statistics under Admin', () => {
    cy.visit('/');
    cy.openSidebar();
    cy.contains('aside nav button', 'Statistics').click();
    cy.url().should('include', '/admin/statistics');
  });

  it('should navigate to data pipeline under Tools', () => {
    cy.visit('/');
    cy.openSidebar();
    cy.contains('aside nav button', 'Data Pipeline').click();
    cy.url().should('include', '/tools/data-pipeline');
  });

  it('should handle browser back button', () => {
    cy.visit('/');
    cy.visit('/repositories');
    cy.go('back');
    cy.url().should('eq', Cypress.config().baseUrl + '/');
  });

  it('should handle browser forward button', () => {
    cy.visit('/');
    cy.visit('/repositories');
    cy.go('back');
    cy.go('forward');
    cy.url().should('include', '/repositories');
  });

  it('should keep layout shell visible across routes', () => {
    cy.visit('/');
    cy.get('header').should('be.visible');
    cy.visit('/repositories');
    cy.get('header').should('be.visible');
  });
});
