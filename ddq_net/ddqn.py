"""
    Double Duelling Deep Q Net with Prioritized Experience Replay
    : Implementation with Doom agent

     https://papers.nips.cc/paper/3964-double-q-learning
"""
import os
from collections import deque
from dataclasses import dataclass, field

import tensorflow as tf
import numpy as np

import vizdoom as vz
from skimage import transform

resolution = (100, 120)
stack_size = 4
VISIBLE = False

STACKED_FRAMES_ = deque(
    [np.zeros(RESOLUTON, dtype=np.int) for _ in range(stack_size)],
    maxlen=4)


def create_env():
    """
        Creates an instance of the game environment
    """
    path = '/usr/local/lib/python3.7/dist-packages/vizdoom/scenarios/'

    doom = vz.DoomGame()
    doom.load_config(os.path.join(path, 'deadly_corridor.cfg'))
    doom.set_doom_scenario_path(os.path.join(path, 'deadly_corridor.wad'))

    doom.set_window_visible(VISIBLE)
    doom.init()

    actions = np.identity(doom.get_available_buttons_size())

    return doom, actions


def preprocess_frame(frame):
    """
        Preprocess the screen buffer for reduced training time
    """
    frame = frame / 255

    return transform.resize(frame[1], (*resolution, 1))


def get_state_size():
    """
        Gives the size of the state (height, width, channels)
    """
    return [*resolution, stack_size]


def stack_frames(state, stacked_frames=None, new_episode=False):
    """
        Creates a stack of four frames from previous
        state for a sense of `motion`
    """
    frame = preprocess_frame(state)

    if new_episode:
        stacked_frames = deque(
            [np.array(frame) for _ in range(stack_size)], maxlen=4)
    else:
        stacked_frames.append(frame)
    state = np.stack(stacked_frames, axis=2)

    return state, stacked_frames


@dataclass
class DoomDDpqN:
    """
        Deep Q Network model for doom.

        Parameters
        ----------
        lr: float
            Learning rate
        gamma: float
            Discounting factor for future rewards
        eps: float
            Explore-exploit tradeoff for agent actions
        min_eps: float
            Minimum value for epsilon
        max_eps: float
            Maxumum value for epsilon
        name: str, default = 'DoomDqNet'
            Variable for tf namescope
        state_size: list, default = [84, 84, 4]
            Shape of input stack
        max_tau: int
            Max C step in updating the target network
    """
    lr: int = 0.0002
    gamma: float = 0.99
    eps: float = 0.00005
    min_eps: float = 0.01
    max_eps: float = 1.0
    memory_size: int = 100000
    name: str = 'DoomDDQN'
    state_size: list = field(default_factory=get_state_size)
    action_size = 7
    max_tau: int = 10000

    def __post_init__(self):
        self.build_model()
        self.memory = Memory(self.memory_size)
        self.setup_writer()

    def build_model(self):
        """
            Builds the Networks to use in training
        """
        with tf.compat.v1.variable_scope(self.name):

            self.inputs = tf.compat.v1.placeholder(
                tf.float32,
                (None, *self.state_size),
                name='inputs')

            self.ISweights = tf.compat.v1.placeholder(
                tf.float32, (None, 1), name='IS_weights')
            self.actions = tf.compat.v1.placeholder(
                tf.float32, (None, self.action_size), name='actions')
            self.target_Q = tf.compat.v1.placeholder(
                tf.float32, (None), name='target')

            self.build_conv_net()

    def build_conv_net(self):
        """
            Creates the model's layers and variables
        """

        conv_one = tf.layers.conv2d(
            inputs=self.inputs,
            filters=32,
            strides=[4, 4],
            kernel_size=(8, 8),
            padding='valid',
            kernel_initializer=tf.contrib.layers.xavier_initializer_conv2d(),
            name='conv_one'
        )
        conv_one_out = tf.nn.relu(inputs=conv_one, name='conv_one_out')

        conv_two = tf.layers.conv2d(
            inputs=conv_one_out,
            filters=64,
            kernel_size=(4, 4),
            strides=(2, 2),
            padding='valid',
            kernel_initializer=tf.contrib.layers.xavier_initializer_conv2d(),
            name='conv_two'
        )
        conv_two_out = tf.nn.relu(
            inputs=conv_two,
            name='conv_two'
        )

        conv_three = tf.layers.conv2d(
            inputs=conv2_out,
            filters=128,
            kernel_size=(4, 4),
            strides=(2, 2),
            padding='valid',
            kernel_initializer=tf.contrib.layers.xavier_initializer_conv2d(),
            name='conv_three'
        )
        conv_three_out = tf.nn.relu(inputs=conv_three, name='conv_three_out')

        flatten = tf.layers.flatten(conv_three_out)
        self.separate_to_streams(flatten)

    def separate_to_streams(self, flatten):
        """
            Creates the Value(s) and Advantage(s, a) layers
        """
        value_fc = tf.layers.dense(
            inputs=flatten,
            activation=tf.nn.relu,
            units=512,
            kernel_initializer=tf.contrib.layers.xavier_initializer(),
            name='value_fc'
        )
        value = tf.layers.dense(
            inputs=value_fc,
            units=1,
            activation=None,
            kernel_initializer=tf.contrib.layers.xavier_initializer(),
            name='value'
        )

        advantg_fc = tf.layers.dense(
            inputs=flatten,
            activation=tf.nn.relu,
            units=512,
            kernel_initializer=tf.contrib.layers.xavier_initializer(),
            name='advantg_fc')
        advantg = tf.layers.dense(
            inputs=advantg_fc,
            activation=None,
            units=self.action_size,
            kernel_size=tf.contrib.layers.xavier_initializer(),
            name='advantage')

    def _dense(self, inputs, units, activation=None, name='', **kwargs):
        """
            Returns a tf dense layer of specified args
        """

        return tf.layers.dense(
            inputs=inputs,
            units=units,
            activation=activation,
            kernel_initializer=kwargs.get('initializer') or
            tf.contrib.layers.xavier_initializer(),
            name=name
        )

    def aggregate(self):
        """
            Defines output and loss
        """

        # Q(s, a):= V(s) + A(s,a) - 1/|A| * sum[A(s,a')]
        self.output = self.value + tf.subtract(
            self.advantg,
            tf.reduce_mean(self.advantg, axis=1, keepdims=True))
        # Predicted Q
        self.Q = tf.reduce_sum(tf.multiply(self.output, self.actions))

        self.abs_errors = tf.abs(self.target_Q - self.Q)
        self.loss = tf.reduce_mean(
            self.ISweights *
            tf.squared_difference(self.target_Q, self.Q))
        self.optimizer = tf.train.AdamOptimizer(self.lr).minimize(self.loss)
