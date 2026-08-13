import fcntl
import os
import socket

from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


# The open descriptor holds the singleton lock for the launch lifetime.
_stack_lock = None


def configure_display():
    """Use the newest VS Code X11 proxy that completes a setup handshake."""
    socket_dir = '/tmp/.X11-unix'
    try:
        candidates = sorted(
            (name for name in os.listdir(socket_dir)
             if name.startswith('X') and name[1:].isdigit()),
            key=lambda name: os.path.getmtime(os.path.join(socket_dir, name)),
            reverse=True,
        )
    except OSError:
        return

    for name in candidates[:10]:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(1)
        try:
            connection.connect(os.path.join(socket_dir, name))
            connection.sendall(
                b'l\x00\x0b\x00\x00\x00\x00\x00\x00\x00\x00\x00')
            reply = connection.recv(8)
            if reply and reply[0] == 1:
                os.environ['DISPLAY'] = f':{name[1:]}'
                return
        except OSError:
            pass
        finally:
            connection.close()


def acquire_stack_lock():
    """Refuse to start a second copy of the complete ROS stack."""
    global _stack_lock
    lock_path = '/tmp/robot_ros_stack.lock'
    _stack_lock = open(lock_path, 'a+', encoding='utf-8')
    try:
        fcntl.flock(_stack_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        _stack_lock.seek(0)
        owner = _stack_lock.read().strip() or 'unknown'
        _stack_lock.close()
        _stack_lock = None
        raise RuntimeError(
            f'ROS2 robot stack is already running (launch PID {owner}). '
            'Stop it before starting another copy.'
        ) from exc

    _stack_lock.seek(0)
    _stack_lock.truncate()
    _stack_lock.write(str(os.getpid()))
    _stack_lock.flush()


def generate_launch_description():
    acquire_stack_lock()
    configure_display()
    code_dir = os.path.dirname(os.path.abspath(__file__))

    imu_bridge = ExecuteProcess(
        cmd=['python3', os.path.join(code_dir, 'imu_bridge.py')],
        cwd=code_dir,
        output='screen',
        respawn=True,
        respawn_delay=2.0,
    )

    lidar_bridge = ExecuteProcess(
        cmd=['python3', os.path.join(code_dir, 'main.py')],
        cwd=code_dir,
        output='screen',
        respawn=True,
        respawn_delay=2.0,
    )

    static_transforms = [
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='imu_static_tf',
            arguments=[
                '--x', '0.0', '--y', '0.0', '--z', '0.0',
                '--yaw', '0.0', '--pitch', '0.0', '--roll', '0.0',
                '--frame-id', 'base_link', '--child-frame-id', 'imu_link',
            ],
        ),
    ]

    odometry = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(code_dir, 'launch_odometry.py')))

    # Wait for sensor and TF producers before starting consumers.
    slam = TimerAction(
        period=2.0,
        actions=[IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(code_dir, 'launch_slam.py')))],
    )

    rviz = TimerAction(
        period=3.0,
        actions=[ExecuteProcess(cmd=['rviz2'], output='screen')],
    )

    return LaunchDescription(
        [imu_bridge, lidar_bridge, *static_transforms, odometry, slam, rviz])
