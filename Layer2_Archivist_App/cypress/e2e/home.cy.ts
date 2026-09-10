describe('TRPL Home Page', () => {
  it('should load the home page successfully', () => {
    cy.visit('http://localhost:5173')
    cy.contains('Home').should('be.visible')
  })
})
