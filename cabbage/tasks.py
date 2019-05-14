import datetime
import functools
import logging
import uuid
from typing import Any, Callable, Dict, Optional, Set

import pendulum

from cabbage import jobs, postgres, store, types

logger = logging.getLogger(__name__)


class Task:
    def __init__(
        self,
        func: Callable,
        *,
        manager: "TaskManager",
        queue: str,
        name: Optional[str] = None,
    ):
        self.queue = queue
        self.manager = manager
        self.func: Callable = func
        self.name = name or self.func.__name__

    def __call__(self, **kwargs: types.JSONValue) -> None:
        return self.func(**kwargs)

    def defer(self, **task_kwargs: types.JSONValue) -> int:
        job_id = self.configure().defer(**task_kwargs)

        return job_id

    def configure(
        self,
        *,
        lock: Optional[str] = None,
        task_kwargs: Optional[types.JSONDict] = None,
        schedule_at: Optional[datetime.datetime] = None,
        schedule_in: Optional[Dict[str, int]] = None,
    ) -> jobs.Job:
        if schedule_at and schedule_in is not None:
            raise ValueError("Cannot set both schedule_at and schedule_in")

        if schedule_in is not None:
            schedule_at = pendulum.now("UTC").add(**schedule_in)

        lock = lock or str(uuid.uuid4())
        task_kwargs = task_kwargs or {}
        return jobs.Job(
            id=None,
            lock=lock,
            task_name=self.name,
            queue=self.queue,
            task_kwargs=task_kwargs,
            scheduled_at=schedule_at,
            job_store=self.manager.job_store,
        )


class TaskManager:
    """
    The TaskManager is the main entrypoint for Cabbage integration.

    Instanciate a single :py:class:`TaskManager` in your code
    and use it to decorate your tasks with :py:func:`TaskManager.task`.

    Yada yada instanciate worker from task manager yada yada

    """

    def __init__(self, job_store: Optional[store.JobStore] = None):
        """
        Parameters
        ----------
        job_store:
            The object in charge of :py:class:`cabbage.jobs.Job` persistance.
            By default, instanciate a :py:class:`cabbage.PostgresJobStore`.

        Returns
        -------
        TaskManager
        """
        if job_store is None:
            job_store = postgres.PostgresJobStore()

        self.job_store = job_store
        self.tasks: Dict[str, Task] = {}
        self.queues: Set[str] = set()

    def task(
        self,
        _func: Optional[Callable] = None,
        queue: str = "default",
        name: Optional[str] = None,
    ) -> Callable:
        """
        Declare a function as a task.

        Can be used as a decorator or a simple method.
        """

        def _wrap(func: Callable) -> Callable[..., Any]:
            task = Task(func, manager=self, queue=queue, name=name)
            self.register(task)

            return functools.update_wrapper(task, func)

        if _func is None:
            return _wrap

        return _wrap(_func)

    def register(self, task: Task) -> None:
        self.tasks[task.name] = task
        if task.queue not in self.queues:
            logger.info(
                "Creating queue (if not already existing)",
                extra={"action": "create_queue", "queue": task.queue},
            )
            self.job_store.register_queue(task.queue)
            self.queues.add(task.queue)