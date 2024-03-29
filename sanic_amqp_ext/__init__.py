from asyncio import ensure_future

from aioamqp import connect as amqp_connect
from sanic_base_ext import BaseExtension


__version__ = '0.2.0'
__all__ = ['AmqpExtension', 'AmqpWorker', ]

VERSION = __version__


class AmqpWorker(object):

    def __init__(self, app, *args, **kwargs):
        self.app = app
        self.protocol = None
        self.transport = None

    async def run(self, *args, **kwargs):
        raise NotImplementedError('`run(*args, **kwargs)` method must be implemented.')

    async def connect(self):
        self.transport, self.protocol = await self.app.ctx.amqp.connect()
        return self.transport, self.protocol

    async def deinit(self):
        if self.protocol:
            if not self.protocol.worker.cancelled():
                self.protocol.worker.cancel()

            await self.protocol.close()

        if self.transport:
            self.transport.close()

        self.transport = None
        self.protocol = None


class AmqpExtension(BaseExtension):
    app_attribute = 'amqp'
    workers = []
    active_tasks = []

    def register_worker(self, worker: AmqpWorker):
        self.workers.append(worker)

    def get_config(self, app):
        return {
            "login": self.get_from_app_config(app, "AMQP_USERNAME", "guest"),
            "password": self.get_from_app_config(app, "AMQP_PASSWORD", "guest"),
            "host": self.get_from_app_config(app, "AMQP_HOST", "localhost"),
            "port": self.get_from_app_config(app, "AMQP_PORT", 5672),
            "virtualhost": self.get_from_app_config(app, "AMQP_VIRTUAL_HOST", "vhost"),
            "ssl": self.get_from_app_config(app, "AMQP_USING_SSL", False),
        }

    async def connect(self):
        config = self.get_config(self.app)
        transport, protocol = await amqp_connect(**config)
        return transport, protocol

    def init_app(self, app, *args, **kwargs):
        super(AmqpExtension, self).init_app(app)

        @app.listener('before_server_start')
        async def aioamqp_configure(app_inner, loop):
            setattr(app_inner.ctx, self.app_attribute, self)

            if not hasattr(app_inner.ctx, 'extensions'):
                setattr(app_inner.ctx, 'extensions', {})
            app_inner.ctx.extensions[self.extension_name] = self

            for worker in self.workers:
                task = ensure_future(worker.run(), loop=loop)
                self.active_tasks.append(task)

        @app.listener('after_server_stop')
        async def aioamqp_free_resources(app_inner, loop):
            for task in self.active_tasks:
                if not loop.is_closed and not task.cancelled():
                    task.cancel()

            for worker in self.workers:
                await worker.deinit()

            setattr(app_inner.ctx, self.app_attribute, None)
            extensions = getattr(app_inner.ctx, 'extensions', {})
            extensions.pop(self.extension_name, None)
