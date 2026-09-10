/// <reference types="cypress" />

const defaultAdminUser = {
  user_id: 'test-admin-001',
  email: 'test.admin@trpl.local',
  name: 'Test Admin',
  display_name: 'Test Admin',
  isAdmin: true,
  isDataFoundations: true,
  isArchivist: true,
  groups: [] as string[],
};

const dashboardStatsBody = {
  totalItems: 1000,
  totalCollections: 50,
  totalPending: 200,
  totalPublishing: 50,
  totalPublished: 600,
  totalRepositories: 5,
  totalReviewed: 100,
  totalErrors: 10,
  overallCompletionRate: 0.75,
};

const repositoryStatsBody = {
  totalItems: 100,
  totalCollections: 5,
  totalPending: 20,
  totalPublishing: 5,
  totalPublished: 60,
  totalReviewed: 10,
  totalErrors: 2,
  overallCompletionRate: 0.75,
};

Cypress.Commands.add('blockMutations', () => {
  (['POST', 'PATCH', 'PUT', 'DELETE'] as const).forEach((method) => {
    cy.intercept(method, '**/api/v1/**', {
      statusCode: 403,
      body: { error: 'blocked in read-only tests' },
    });
  });
});

Cypress.Commands.add('mockAuth', (overrides: Record<string, unknown> = {}) => {
  cy.intercept('GET', '**/api/v1/auth/me', {
    statusCode: 200,
    body: {
      authenticated: true,
      user: { ...defaultAdminUser, ...overrides },
    },
  }).as('authMe');
});

/**
 * Registers a generic API fallback first, then specific route mocks (last wins in Cypress).
 */
Cypress.Commands.add('stubReadOnlyApi', () => {
  cy.intercept('GET', '**/api/v1/**', { statusCode: 200, body: {} });

  cy.intercept('GET', '**/api/v1/dashboard/statistics', {
    statusCode: 200,
    body: dashboardStatsBody,
  });

  const repositoryListBody = {
    repositories: [
      {
        repository: 'Test Repo',
        totalItems: 100,
        collections: 5,
        pending: 20,
        reviewed: 10,
        publishing: 5,
        published: 60,
        errors: 2,
        completionRate: 0.75,
      },
    ],
    count: 1,
  };

  cy.intercept('GET', '**/api/v1/repositories?**', {
    statusCode: 200,
    body: repositoryListBody,
  });

  cy.intercept('GET', '**/api/v1/repositories/statistics', {
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
  });

  cy.intercept('GET', '**/api/v1/repositories/*/statistics', {
    statusCode: 200,
    body: repositoryStatsBody,
  });

  cy.intercept('GET', '**/api/v1/repositories/*/collections**', {
    statusCode: 200,
    body: {
      collections: [
        {
          collection: 'Test Coll',
          repository: 'Test Repo',
          totalItems: 10,
          pending: 2,
          published: 6,
          reviewed: 1,
          publishing: 0,
          errors: 0,
        },
      ],
      count: 1,
    },
  });

  cy.intercept('GET', '**/api/v1/collections/**', {
    statusCode: 200,
    body: { summaries: [], collections: [], count: 0 },
  });

  cy.intercept('GET', '**/api/v1/filters/**', {
    statusCode: 200,
    body: {
      repositories: ['Test Repo'],
      collections: ['Test Coll'],
      resource_types: ['correspondence'],
      sources: [],
      creators: [],
      recipients: [],
    },
  });

  cy.intercept('GET', '**/api/v1/documents**', {
    statusCode: 200,
    body: {
      documents: [
        {
          id: 'doc-test-1',
          metadata: {
            Title: 'Route Test Doc',
            Repository: 'Test Repo',
            Collection: 'Test Coll',
          },
          status: 'pending',
          assets: [],
        },
      ],
      count: 1,
    },
  });

  cy.intercept('GET', '**/api/v1/statistics/**', {
    statusCode: 200,
    body: { statisticsExist: true, message: 'ok', lastUpdated: null },
  });

  cy.intercept('GET', '**/api/v1/pipeline/jobs**', {
    statusCode: 200,
    body: { jobs: [], count: 0 },
  });

  cy.intercept('GET', '**/api/v1/pipeline/periodic-**', {
    statusCode: 200,
    body: { schedules: [], runs: [], count: 0 },
  });

  cy.intercept('GET', '**/api/v1/pipeline/statistics', {
    statusCode: 200,
    body: {
      total_records: 0,
      by_stage: {},
      overall: { pending: 0, completed: 0, error: 0 },
      active_batches: 0,
    },
  });

  cy.intercept('GET', '**/api/v1/pipeline/stages', {
    statusCode: 200,
    body: {
      stages: [],
      actions: {
        full_pipeline: {
          name: 'Full Pipeline',
          description: 'Run all stages',
          trigger_endpoint: 'full-pipeline',
        },
        retry_failed: {
          name: 'Retry Failed',
          description: 'Retry failed records',
          trigger_endpoint: 'retry-failed',
        },
      },
      runtime_config: {
        collection_ids: [],
        batch_size: 100,
        parallel_batches: 20,
      },
    },
  });

  cy.intercept('GET', '**/api/v1/ingestion/**', {
    statusCode: 200,
    body: { failed_count: 0, publishers: [], batches: [], count: 0 },
  });

  cy.intercept('GET', '**/api/v1/epub/**', {
    statusCode: 200,
    body: { documents: [], count: 0, page: 1, page_size: 50, total_pages: 0 },
  });

  cy.intercept('GET', '**/api/v1/correction-requests**', {
    statusCode: 200,
    body: { items: [], total: 0 },
  });

  cy.intercept('GET', '**/api/v1/field-mappings**', {
    statusCode: 200,
    body: { mappings: [], count: 0 },
  });

  cy.intercept('GET', '**/api/v1/metadata-fields**', {
    statusCode: 200,
    body: { fields: [], count: 0 },
  });

  cy.intercept('GET', '**/api/v1/field-mappings/resolve**', {
    statusCode: 200,
    body: { field_name: 'Identifier' },
  });

  cy.intercept('GET', '**/api/v1/documents/*/audit**', {
    statusCode: 200,
    body: { entries: [], count: 0 },
  });

  cy.intercept('GET', '**/api/v1/documents/doc-test-1**', {
    statusCode: 200,
    body: {
      id: 'doc-test-1',
      metadata: {
        Title: 'Route Test Doc',
        Repository: 'Test Repo',
        Collection: 'Test Coll',
      },
      status: 'pending',
      asset_details: [],
      assets: [],
    },
  });
});

Cypress.Commands.add('openSidebar', () => {
  cy.get('[aria-label="Toggle menu"]').click();
  cy.get('aside nav').should('be.visible');
});

Cypress.Commands.add('clickSidebar', (label: string) => {
  cy.openSidebar();
  cy.contains('aside nav button', label).click();
});

declare global {
  namespace Cypress {
    interface Chainable {
      blockMutations(): Chainable<void>;
      mockAuth(overrides?: Record<string, unknown>): Chainable<void>;
      stubReadOnlyApi(): Chainable<void>;
      openSidebar(): Chainable<void>;
      clickSidebar(label: string): Chainable<void>;
    }
  }
}

export {};
