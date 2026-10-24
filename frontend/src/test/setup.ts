// Shared setup of the Vitest suite (tests only: not part of the build or the Docker image).
//
// waitFor() and findBy*() give up after 1 s by default. With many test files running in
// parallel, the FIRST test of a file also pays the loading of the application modules, and
// on a busy machine some correct waits took 1.1-1.2 s (cart, bell counter, checkout). They
// failed although the page rendered exactly what was expected.
// 3 s leaves room for that start-up cost. A condition that never becomes true still fails,
// and the tests keep waiting for real conditions, never for a fixed delay.
import { configure } from '@testing-library/dom';

configure({ asyncUtilTimeout: 3000 });
