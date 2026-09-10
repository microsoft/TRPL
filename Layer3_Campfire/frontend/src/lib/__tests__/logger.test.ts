/**
 * @jest-environment node
 */

// Need to test with different NODE_ENV values, so we'll mock and reset modules

describe('logger', () => {
  const originalEnv = process.env.NODE_ENV

  beforeEach(() => {
    jest.resetModules()
    jest.spyOn(console, 'log').mockImplementation(() => {})
    jest.spyOn(console, 'warn').mockImplementation(() => {})
    jest.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    ;(process.env as Record<string, unknown>).NODE_ENV = originalEnv
    jest.restoreAllMocks()
  })

  describe('in development', () => {
    beforeEach(() => {
      ;(process.env as Record<string, unknown>).NODE_ENV = 'development'
    })

    it('logs debug messages with console.log', async () => {
      jest.resetModules()
      const { logger } = await import('../logger')

      logger.debug('test message')

      expect(console.log).toHaveBeenCalledWith('[DEBUG] test message', '')
    })

    it('logs info messages with console.log', async () => {
      jest.resetModules()
      const { logger } = await import('../logger')

      logger.info('test message')

      expect(console.log).toHaveBeenCalledWith('[INFO] test message', '')
    })

    it('logs warn messages with console.warn', async () => {
      jest.resetModules()
      const { logger } = await import('../logger')

      logger.warn('test message')

      expect(console.warn).toHaveBeenCalledWith('[WARN] test message', '')
    })

    it('logs error messages with console.error', async () => {
      jest.resetModules()
      const { logger } = await import('../logger')

      logger.error('test message')

      expect(console.error).toHaveBeenCalledWith('[ERROR] test message', '')
    })

    it('includes context in log output', async () => {
      jest.resetModules()
      const { logger } = await import('../logger')
      const context = { userId: '123', action: 'login' }

      logger.info('user action', context)

      expect(console.log).toHaveBeenCalledWith('[INFO] user action', context)
    })
  })

  describe('in production', () => {
    beforeEach(() => {
      ;(process.env as Record<string, unknown>).NODE_ENV = 'production'
    })

    it('does not log debug messages', async () => {
      jest.resetModules()
      const { logger } = await import('../logger')

      logger.debug('test message')

      expect(console.log).not.toHaveBeenCalled()
    })

    it('does not log info messages', async () => {
      jest.resetModules()
      const { logger } = await import('../logger')

      logger.info('test message')

      expect(console.log).not.toHaveBeenCalled()
    })

    it('logs warn messages', async () => {
      jest.resetModules()
      const { logger } = await import('../logger')

      logger.warn('test message')

      expect(console.warn).toHaveBeenCalled()
    })

    it('logs error messages', async () => {
      jest.resetModules()
      const { logger } = await import('../logger')

      logger.error('test message')

      expect(console.error).toHaveBeenCalled()
    })
  })
})
