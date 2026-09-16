// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @integration — hits live API; excluded from default `npm run cypress:run`.
 * Requires local API + document in Cosmos DB.
 * Set CYPRESS_REVIEW_DOCUMENT_ID to the document fixture to load.
 */
describe('@integration Review Page - Load Document', () => {

  const documentId = Cypress.env('REVIEW_DOCUMENT_ID');

  if (!documentId) {
    throw new Error(
      'Set CYPRESS_REVIEW_DOCUMENT_ID to the ID of an existing read-only test document.'
    );
  }

  beforeEach(() => {
  cy.intercept('GET', '**/review/**').as('getReview');
  cy.visit(`/review/${documentId}`);
  cy.wait('@getReview');
});
  it('should load the review page successfully', () => {
    cy.url().should('include', `/review/${documentId}`);
    cy.contains('Review').should('be.visible');
  });

  it('should display correct document header details', () => {
    cy.get('[data-testid="document-title"]')
      .should('be.visible')
      .and('not.be.empty');

    cy.get('[data-testid="repository-name"]')
      .should('be.visible')
      .and('not.be.empty');

    cy.get('[data-testid="collection-name"]')
      .should('be.visible')
      .and('not.be.empty');
  });

  it('should display Pending status badge', () => {
    cy.get('[data-testid="status-badge"]')
      .should('be.visible')
      .and('contain.text', 'Pending');
  });

  it('should load asset thumbnails', () => {
    cy.get('[data-testid="asset-thumbnail"]')
      .should('have.length.greaterThan', 0);
  });

  it('should load full image when an asset is selected', () => {
    cy.get('[data-testid="asset-thumbnail"]').first().click();

    cy.get('[data-testid="asset-viewer-image"]')
      .should('be.visible')
      .and(($img) => {
        expect($img[0].naturalWidth).to.be.greaterThan(0);
      });
  });

  it('should display OCR text and metadata fields', () => {
    cy.get('[data-testid="ocr-original-text"]')
      .should('be.visible')
      .and('not.be.empty');

    cy.get('[data-testid="ocr-modified-text"]')
      .should('be.visible')
      .and('not.be.empty');

    cy.get('[data-testid^="metadata-field-"]')
      .each(($field) => {
        cy.wrap($field).should('not.have.value', '');
      });
  });

});
