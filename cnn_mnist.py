from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import gzip
import os
import sys
import time

import numpy
from six.moves import urllib
from six.moves import xrange  # pylint: disable=redefined-builtin
import tensorflow as tf

MNIST_URL = 'http://yann.lecun.com/exdb/mnist/'
WORK_DIRECTORY = 'data'
IMAGE_SIZE = 28
NUM_CHANNELS = 1
PIXEL_DEPTH = 255
SEED = 66478  # Set to None for random seed.
EVAL_FREQUENCY = 100  # Number of steps between evaluations.

tf.app.flags.DEFINE_string("dataset", "mnist", "Choices: mnist, mnist550, mnist100, letters")
tf.app.flags.DEFINE_string("target", "label", "Choices: label, prob, prob_l2, logit, logitp")
tf.app.flags.DEFINE_integer("train_size", -1, "Size of training set. Set to negative to use all.")
tf.app.flags.DEFINE_integer("val_size", -1, "Size of validation set. Set to negative to use all.")
tf.app.flags.DEFINE_integer("test_size", -1, "Size of testing set. Set to negative to use all")
tf.app.flags.DEFINE_integer("num_epochs", 10, "Number of epochs.")
tf.app.flags.DEFINE_integer("batch_size", 64, "Batch size at training.")
tf.app.flags.DEFINE_integer("eval_batch_size", 512, "Batch size at evaluation.")
tf.app.flags.DEFINE_integer("num_lr_stages", 4, "Number of different learning rate.")
tf.app.flags.DEFINE_integer("train_ver", 0, "Training set version.")
tf.app.flags.DEFINE_float("init_lr", 0.01, "Initial learning rate.")
tf.app.flags.DEFINE_float("lr_decay", 0.1, "Learning rate decay.")
tf.app.flags.DEFINE_string("load_logit_from", "", "File name under data/ for loading logits.")
tf.app.flags.DEFINE_string("save_logit_to", "", "File name under data/ for saving/loading logits.")
tf.app.flags.DEFINE_string("load_prob_from", "", "File name under data/ for loading probs.")
tf.app.flags.DEFINE_string("save_prob_to", "", "File name under data/ for saving/loading probs.")
tf.app.flags.DEFINE_string("load_model_from", "", "File name under data/ for loading model.")
tf.app.flags.DEFINE_string("save_model_to", "", "File name under data/ for saving model.")
tf.app.flags.DEFINE_boolean("load_full_model", False, "Load all layers incl. classification if True")
FLAGS = tf.app.flags.FLAGS


def maybe_download(filename):
    """Download MNIST from Yann's website, unless it's already here."""
    if not tf.gfile.Exists(WORK_DIRECTORY):
        tf.gfile.MakeDirs(WORK_DIRECTORY)
    filepath = os.path.join(WORK_DIRECTORY, filename)
    if not tf.gfile.Exists(filepath):
        filepath, _ = urllib.request.urlretrieve(MNIST_URL + filename, filepath)
        with tf.gfile.GFile(filepath) as f:
            size = f.Size()
        print('Successfully downloaded', filename, size, 'bytes.')
    return filepath


def extract_data(filename, num_images, offset=0):
    """Extract the images into a 4D tensor [image index, y, x, channels].

    Values are rescaled from [0, 255] down to [-0.5, 0.5].
    """
    print('Extracting %s (offset=%d)' % (filename, offset))
    with gzip.open(filename) as bytestream:
        bytestream.seek(16 + IMAGE_SIZE * IMAGE_SIZE * offset)
        buf = bytestream.read(IMAGE_SIZE * IMAGE_SIZE * num_images)
        data = numpy.frombuffer(buf, dtype=numpy.uint8).astype(numpy.float32)
        data = (data - (PIXEL_DEPTH / 2.0)) / PIXEL_DEPTH
        data = data.reshape(num_images, IMAGE_SIZE, IMAGE_SIZE, 1)
    return data


def extract_labels(filename, num_images, offset=0):
    """Extract the labels into a vector of int64 label IDs."""
    print('Extracting %s (offset=%d)' % (filename, offset))
    with gzip.open(filename) as bytestream:
        bytestream.seek(8 + offset)
        buf = bytestream.read(1 * num_images)
        labels = numpy.frombuffer(buf, dtype=numpy.uint8).astype(numpy.int64)
    return labels


def extract_logits(filename, num_images=-1, offset=0):
    """Extract the logits or posteriors into a 2D tensor with shape (n_images, n_classes)"""
    filepath = os.path.join(WORK_DIRECTORY, filename)
    print('Extracting %s (offset=%d)' % (filepath, offset))
    logits = numpy.load(filepath)
    if num_images != -1:
        logits = logits[offset:offset + num_images, :NUM_LABELS]
    else:
        logits = logits[offset:, :NUM_LABELS]
    return logits


def error_rate(predictions, labels):
    """Return the error rate based on dense predictions and sparse labels."""
    return 100.0 - (
        100.0 *
        numpy.sum(numpy.argmax(predictions, 1) == labels) /
        predictions.shape[0])


def tf_logit(p):
    return tf.log(p + 1e-12) - tf.log(1 - p + 1e-12)


# Saves memory and enables this to run on smaller GPUs.
def eval_in_batches(target, data_node, data, sess):
    """Get all predictions for a dataset by running it in small batches."""
    size = data.shape[0]
    if size < FLAGS.eval_batch_size:
        raise ValueError("batch size for evals larger than dataset: %d" % size)
    predictions = numpy.ndarray(shape=(size, NUM_LABELS), dtype=numpy.float32)
    for begin in xrange(0, size, FLAGS.eval_batch_size):
        end = begin + FLAGS.eval_batch_size
        if end <= size:
            predictions[begin:end, :] = sess.run(
                target,
                feed_dict={data_node: data[begin:end, ...]})
        else:
            batch_predictions = sess.run(
                target,
                feed_dict={data_node: data[-FLAGS.eval_batch_size:, ...]})
            predictions[begin:, :] = batch_predictions[begin - size:, :]
    return predictions


def dataset(dataset_name):
    if dataset_name == 'mnist':
        # Get the data.
        train_data_filename = maybe_download('train-images-idx3-ubyte.gz')
        train_labels_filename = maybe_download('train-labels-idx1-ubyte.gz')
        test_data_filename = maybe_download('t10k-images-idx3-ubyte.gz')
        test_labels_filename = maybe_download('t10k-labels-idx1-ubyte.gz')
        # Extract it into numpy arrays.
        train_data = extract_data(train_data_filename, 55000)
        train_labels = extract_labels(train_labels_filename, 55000)
        val_data = extract_data(train_data_filename, 5000, 55000)
        val_labels = extract_labels(train_labels_filename, 5000, 55000)
        test_data = extract_data(test_data_filename, 10000)
        test_labels = extract_labels(test_labels_filename, 10000)
    elif dataset_name == 'mnist550':
        assert (FLAGS.train_ver < 100)
        # Get the data.
        train_data_filename = maybe_download('mnist-images-idx3-ubyte.gz')
        train_labels_filename = maybe_download('mnist-labels-idx1-ubyte.gz')
        test_data_filename = maybe_download('t10k-images-idx3-ubyte.gz')
        test_labels_filename = maybe_download('t10k-labels-idx1-ubyte.gz')
        # Extract it into numpy arrays.
        train_data = extract_data(train_data_filename, 550, FLAGS.train_ver * 550)
        train_labels = extract_labels(train_labels_filename, 550, FLAGS.train_ver * 550)
        val_data = extract_data(train_data_filename, 5000, 55000)
        val_labels = extract_labels(train_labels_filename, 5000, 55000)
        test_data = extract_data(test_data_filename, 10000)
        test_labels = extract_labels(test_labels_filename, 10000)
    elif dataset_name == 'mnist100':
        assert (FLAGS.train_ver < 550)
        # Get the data.
        train_data_filename = maybe_download('mnist-images-idx3-ubyte.gz')
        train_labels_filename = maybe_download('mnist-labels-idx1-ubyte.gz')
        test_data_filename = maybe_download('t10k-images-idx3-ubyte.gz')
        test_labels_filename = maybe_download('t10k-labels-idx1-ubyte.gz')
        # Extract it into numpy arrays.
        train_data = extract_data(train_data_filename, 100, FLAGS.train_ver * 100)
        train_labels = extract_labels(train_labels_filename, 100, FLAGS.train_ver * 100)
        val_data = extract_data(train_data_filename, 5000, 55000)
        val_labels = extract_labels(train_labels_filename, 5000, 55000)
        test_data = extract_data(test_data_filename, 10000)
        test_labels = extract_labels(test_labels_filename, 10000)
    elif dataset_name == 'letters':
        # Get the data.
        letters_data_filename = maybe_download('letters-images-idx3-ubyte.gz')
        letters_labels_filename = maybe_download('letters-labels-idx1-ubyte.gz')
        # Extract it into numpy arrays.
        train_data = extract_data(letters_data_filename, 330000)
        train_labels = extract_labels(letters_labels_filename, 330000)
        val_data = extract_data(letters_data_filename, 26000, 330000)
        val_labels = extract_labels(letters_labels_filename, 26000, 330000)
        test_data = extract_data(letters_data_filename, 52000, 356000)
        test_labels = extract_labels(letters_labels_filename, 52000, 356000)
    else:
        raise ValueError("Unknow dataset \"%s\"" % dataset_name)

    return train_data, train_labels, val_data, val_labels, test_data, test_labels


def model(data, label=None, train=False):
    """The Model definition."""
    tf.get_variable_scope().set_initializer(
        tf.truncated_normal_initializer(stddev=0.1, seed=SEED))
    var_list_wo_last = []
    regularizers = 0
    with tf.variable_scope('conv1'):
        filters = tf.get_variable('filters', [5, 5, NUM_CHANNELS, 32])
        biases = tf.get_variable('biases', [32],
                                 initializer=tf.constant_initializer(0.0))
        var_list_wo_last.append(filters)
        var_list_wo_last.append(biases)
        conv = tf.nn.conv2d(data,
                            filters,
                            strides=[1, 1, 1, 1],
                            padding='SAME')
        relu = tf.nn.relu(tf.nn.bias_add(conv, biases))
        pool = tf.nn.max_pool(relu,
                              ksize=[1, 2, 2, 1],
                              strides=[1, 2, 2, 1],
                              padding='SAME')
    with tf.variable_scope('conv2'):
        filters = tf.get_variable('filters', [5, 5, 32, 64])
        biases = tf.get_variable('biases', [64],
                                 initializer=tf.constant_initializer(0.1))
        var_list_wo_last.append(filters)
        var_list_wo_last.append(biases)
        conv = tf.nn.conv2d(pool,
                            filters,
                            strides=[1, 1, 1, 1],
                            padding='SAME')
        relu = tf.nn.relu(tf.nn.bias_add(conv, biases))
        pool = tf.nn.max_pool(relu,
                              ksize=[1, 2, 2, 1],
                              strides=[1, 2, 2, 1],
                              padding='SAME')
        pool_shape = pool.get_shape().as_list()
        pool = tf.reshape(pool,
                          [pool_shape[0], pool_shape[1] * pool_shape[2] * pool_shape[3]])
    with tf.variable_scope('fc1'):
        filters = tf.get_variable('filters', [IMAGE_SIZE // 4 * IMAGE_SIZE // 4 * 64, 512])
        biases = tf.get_variable('biases', [512],
                                 initializer=tf.constant_initializer(0.1))
        var_list_wo_last.append(filters)
        var_list_wo_last.append(biases)
        regularizers += (tf.nn.l2_loss(filters) + tf.nn.l2_loss(biases))
        hidden = tf.nn.relu(tf.matmul(pool, filters) + biases)
        if train:
            hidden = tf.nn.dropout(hidden, 0.5, seed=SEED)
    with tf.variable_scope('fc2'):
        filters = tf.get_variable('filters', [512, NUM_LABELS])
        biases = tf.get_variable('biases', [NUM_LABELS],
                                 initializer=tf.constant_initializer(0.1))
        regularizers += (tf.nn.l2_loss(filters) + tf.nn.l2_loss(biases))
        logits = tf.matmul(hidden, filters) + biases

    # loss function
    if label is None:
        loss = 0
    else:
        if FLAGS.target == "prob":
            loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
                logits, label))
        elif FLAGS.target == "prob_l2":
            loss = tf.nn.l2_loss(tf.nn.softmax(logits) - label) / FLAGS.batch_size
        elif FLAGS.target == "logit":
            loss = tf.nn.l2_loss(logits - label) / FLAGS.batch_size
        elif FLAGS.target == "logitp":
            loss = tf.nn.l2_loss(logits - tf_logit(label)) \
                   / FLAGS.batch_size
        else:
            assert (FLAGS.target == "label")
            loss = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(
                logits, label))

        # Add the regularization term to the loss.
        loss += 5e-4 * regularizers

    return logits, loss, var_list_wo_last


def main(argv=None):  # pylint: disable=unused-argument
    dataset_names = FLAGS.dataset.split('+')
    train_data_list = []
    train_labels_list = []
    val_data_list = []
    val_labels_list = []
    test_data_list = []
    test_labels_list = []
    label_ranges = []
    label_offset = 0
    for dataset_name in dataset_names:
        train_data, train_labels, val_data, val_labels, test_data, test_labels = dataset(dataset_name)
        train_data_list.append(train_data)
        train_labels_list.append(train_labels + label_offset)
        val_data_list.append(val_data)
        val_labels_list.append(val_labels + label_offset)
        test_data_list.append(test_data)
        test_labels_list.append(test_labels + label_offset)
        label_ranges.append(max(max(train_labels), max(val_labels), max(test_labels)) + 1)
        label_offset += label_ranges[-1]
    train_data = numpy.concatenate(train_data_list, axis=0)
    train_labels = numpy.concatenate(train_labels_list, axis=0)
    val_data = numpy.concatenate(val_data_list, axis=0)
    val_labels = numpy.concatenate(val_labels_list, axis=0)
    test_data = numpy.concatenate(test_data_list, axis=0)
    test_labels = numpy.concatenate(test_labels_list, axis=0)

    train_data_0 = train_data

    # shuffle data and labels
    order_idxs_train = numpy.random.permutation(numpy.arange(train_labels.shape[0]))
    train_data = train_data[order_idxs_train, :, :, :]
    train_labels = train_labels[order_idxs_train]
    order_idxs_val = numpy.random.permutation(numpy.arange(val_labels.shape[0]))
    val_data = val_data[order_idxs_val, :, :, :]
    val_labels = val_labels[order_idxs_val]
    order_idxs_test = numpy.random.permutation(numpy.arange(test_labels.shape[0]))
    test_data = test_data[order_idxs_test, :, :, :]
    test_labels = test_labels[order_idxs_test]

    global NUM_LABELS
    NUM_LABELS = max(train_labels) + 1

    # Reduce sample numbers if requested
    if FLAGS.train_size >= 0:
        train_data = train_data[:FLAGS.train_size, ...]
        train_labels = train_labels[:FLAGS.train_size]
    if FLAGS.val_size >= 0:
        val_data = val_data[:FLAGS.val_size, ...]
        val_labels = val_labels[:FLAGS.val_size]
    if FLAGS.test_size >= 0:
        test_data = test_data[:FLAGS.test_size, ...]
        test_labels = test_labels[:FLAGS.test_size]

    train_size = train_labels.shape[0]
    val_size = val_labels.shape[0]
    test_size = test_labels.shape[0]

    num_epochs = FLAGS.num_epochs

    # Training probs/logits
    if FLAGS.target != "label":
        if FLAGS.target == "logit" or FLAGS.target == "logitp":
            train_logits = extract_logits(FLAGS.load_logit_from, train_size)
        else:
            assert (FLAGS.target == "prob" or FLAGS.target == "prob_l2")
            train_logits = extract_logits(FLAGS.load_prob_from, train_size)
        train_logits = train_logits[order_idxs_train, :]
        train_logits = train_logits[:train_size]
        gt_accuracy = numpy.sum((numpy.argmax(train_logits, 1) == train_labels).astype(numpy.int64)) \
                      / train_labels.shape[0]
        print("gt accuracy: %.2f%%" % (100 * float(gt_accuracy)))

    # data & label nodes
    train_data_node = tf.placeholder(
        tf.float32,
        shape=(FLAGS.batch_size, IMAGE_SIZE, IMAGE_SIZE, NUM_CHANNELS))
    eval_data_node = tf.placeholder(
        tf.float32,
        shape=(FLAGS.eval_batch_size, IMAGE_SIZE, IMAGE_SIZE, NUM_CHANNELS))
    if FLAGS.target == "label":
        train_labels_node = tf.placeholder(tf.int64, shape=(FLAGS.batch_size,))
    else:
        train_labels_node = tf.placeholder(tf.float32, shape=(FLAGS.batch_size, NUM_LABELS))

    # Training computation: logits + cross-entropy loss.
    with tf.variable_scope("cnn"):
        logits, loss, var_list_wo_last = model(train_data_node, train_labels_node, True)

    # Optimizer: set up a variable that's incremented once per batch and
    # controls the learning rate decay.
    batch = tf.Variable(0)

    # Decay using an exponential schedule starting at 0.01.
    learning_rate = tf.train.exponential_decay(
        FLAGS.init_lr,  # Base learning rate.
        batch * FLAGS.batch_size,  # Current index into the dataset.
        (train_size * FLAGS.num_epochs) // FLAGS.num_lr_stages,  # Decay step.
        FLAGS.lr_decay,  # Decay rate.
        staircase=True)

    # Use simple momentum for the optimization.
    optimizer = tf.train.MomentumOptimizer(learning_rate,
                                           0.9).minimize(loss,
                                                         global_step=batch)

    # Predictions for the current training minibatch.
    train_prediction = tf.nn.softmax(logits)

    # Predictions for the test and validation, which we'll compute less often.
    with tf.variable_scope("cnn", reuse=True):
        eval_logit, _, _ = model(eval_data_node)
    eval_prediction = tf.nn.softmax(eval_logit)

    # Add ops to save and restore all the variables.
    saver = tf.train.Saver()

    # Create a local session to run the training.
    start_time = time.time()
    with tf.Session() as sess:
        # Run all the initializers to prepare the trainable parameters.
        tf.initialize_all_variables().run()
        print("Model initialized.")

        # Restore variables from disk.
        if len(FLAGS.load_model_from) != 0:
            if FLAGS.load_full_model:
                loader = tf.train.Saver()
            else:
                loader = tf.train.Saver(var_list=var_list_wo_last)
            loader.restore(sess, os.path.join(WORK_DIRECTORY, FLAGS.load_model_from))
            sess.run(tf.assign(batch, 0))
            print("Model restored.")

        # Loop through training steps.
        if train_size > 0:
            for step in xrange(int(num_epochs * train_size) // FLAGS.batch_size):
                # Compute the offset of the current minibatch in the data.
                # Note that we could use better randomization across epochs.
                offset = (step * FLAGS.batch_size) % (train_size - FLAGS.batch_size)
                batch_data = train_data[offset:(offset + FLAGS.batch_size), ...]
                batch_labels = train_labels[offset:(offset + FLAGS.batch_size)]
                if FLAGS.target == 'label':
                    # This dictionary maps the batch data (as a numpy array) to the
                    # node in the graph is should be fed to.
                    feed_dict = {train_data_node: batch_data,
                                 train_labels_node: batch_labels}
                else:
                    batch_logits = train_logits[offset:(offset + FLAGS.batch_size), :]
                    # This dictionary maps the batch data (as a numpy array) to the
                    # node in the graph is should be fed to.
                    feed_dict = {train_data_node: batch_data,
                                 train_labels_node: batch_logits}

                # Run the graph and fetch some of the nodes.
                _, l, lr, predictions = sess.run(
                    [optimizer, loss, learning_rate, train_prediction],
                    feed_dict=feed_dict)
                if step % EVAL_FREQUENCY == 0:
                    elapsed_time = time.time() - start_time
                    start_time = time.time()
                    print('Step %d (epoch %.2f), %.1f iters (%.1f images) per second' %
                          (step, float(step) * FLAGS.batch_size / train_size,
                           EVAL_FREQUENCY / elapsed_time, EVAL_FREQUENCY * FLAGS.batch_size / elapsed_time))
                    print('Minibatch loss: %.3f, learning rate: %.6f' % (l, lr))
                    print('Minibatch error: %.1f%%' % error_rate(predictions, batch_labels))
                    if val_size > 0:
                        print('Validation error: %.1f%%' % error_rate(
                            eval_in_batches(eval_prediction, eval_data_node, val_data, sess), val_labels))
                    sys.stdout.flush()

        # Save posteriors of training samples
        if len(FLAGS.save_prob_to) > 0:
            train_probs = eval_in_batches(eval_prediction, eval_data_node, train_data_0, sess)
            numpy.save(os.path.join(WORK_DIRECTORY, FLAGS.save_prob_to), train_probs)
        if len(FLAGS.save_logit_to) > 0:
            train_logits = eval_in_batches(eval_logit, eval_data_node, train_data_0, sess)
            numpy.save(os.path.join(WORK_DIRECTORY, FLAGS.save_logit_to), train_logits)

        # Finally print the result!
        if test_size > 0:
            test_probs = eval_in_batches(eval_prediction, eval_data_node, test_data, sess)
            if len(label_ranges) == 1:
                print('Test error: %.1f%%' % error_rate(test_probs, test_labels))
            else:
                print('Test error:', end='')
                for dataset_idx, label_subset_range in enumerate(label_ranges):
                    label_begin = sum(label_ranges[:dataset_idx])
                    label_end = sum(label_ranges[:dataset_idx + 1])
                    label_mask = (test_labels >= label_begin) * (test_labels < label_end)
                    print(' %s-%.1f%%' % (dataset_names[dataset_idx], error_rate(
                        test_probs[label_mask, label_begin:label_end], test_labels[label_mask] - label_begin)), end='')
                print(' overall-%.1f%%' % error_rate(test_probs, test_labels))

        # Save model
        if len(FLAGS.save_model_to) != 0:
            save_path = saver.save(sess, os.path.join(WORK_DIRECTORY, FLAGS.save_model_to))
            print("Model saved in file: %s" % save_path)


if __name__ == '__main__':
    tf.app.run()
